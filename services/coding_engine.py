"""
JARVIS Coding Engine — JARVIS entwickelt sich selbst, über das Claude Agent SDK.

Läuft als eigener Hintergrund-Thread pro Task (analog zu services/proactive.py),
nie auf dem asyncio-Haupt-Loop von server.py. Arbeitet in einem eigenen
`git worktree` (eigener Branch, eigenes Arbeitsverzeichnis) — NICHT im
gemeinsam genutzten Haupt-Checkout (WORKSPACE_ROOT), damit Simons/Claude Codes
eigene, noch uncommittete Arbeit dort nie berührt wird und der Branch-Wechsel
nicht den für alle sichtbaren Checkout umschaltet.

Freigabe-Fluss: can_use_tool() blockiert (in einem Executor-Thread, nicht auf
dem Coding-Engine-eigenen Event-Loop) bis server.py per resolve_approval()
eine Antwort auf CODING_APPROVAL_REQUEST liefert.
"""
import asyncio
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import protocol as P
import tracking

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
_WORKTREE_BASE = Path.home() / ".jarvis" / "coding_worktrees"
_GIT_AUTHOR = ["-c", "user.name=JARVIS Coding-Engine", "-c", "user.email=jarvis@localhost"]

_manager = None
_dispatcher = None

_task_lock = threading.Lock()
_task_running = False

_status_lock = threading.Lock()
_current_status: dict = {"active": False}

_approval_lock = threading.Lock()
_pending_events: dict[str, threading.Event] = {}
_pending_decisions: dict[str, bool] = {}
_APPROVAL_TIMEOUT_SEC = 600

_DESTRUCTIVE_PATTERNS = (
    "rm -rf", "git push --force", "git push -f", "sudo ",
    "pip install", "pip3 install", "npm install", "npm i ",
    "curl ", "wget ",
)
_SECRET_PATTERNS = (".env", ".key", "id_rsa", "credentials")

_MAX_TURNS_SAFETY = 40  # Sekundäres Netz falls max_budget_usd die Kosten-Schätzung mal daneben liegt

_TRACKING_TOPIC = "coding_engine"

# Bewusst knapp statt des vollen Claude-Code-Presets — nur die Konventionen aus
# CLAUDE.md, die für automatisierte Einzel-Tasks wirklich relevant sind.
_SYSTEM_PROMPT = (
    "Du bist JARVIS' eigene Coding-Engine, ein autonomer Hintergrund-Task ohne "
    "Live-Rückfragemöglichkeit an Simon. Du arbeitest im j.a.r.v.i.s.-Server-Repo "
    "(Python asyncio WebSocket-Server) auf einem bereits ausgecheckten eigenen "
    "Git-Branch (niemals main). Konventionen: Print-Ausgaben mit Prefix "
    "[modulname] und flush=True; Kommentare Deutsch oder Englisch; Services "
    "sind isoliert, keine Cross-Service-Imports. Halte Änderungen minimal und "
    "exakt auf die gestellte Aufgabe fokussiert — keine Refactorings oder "
    "Zusatzänderungen ohne Auftrag. Committe deine Änderungen nicht selbst, "
    "das übernimmt die aufrufende Umgebung."
)


def init(client_manager, dispatcher) -> None:
    global _manager, _dispatcher
    _manager = client_manager
    _dispatcher = dispatcher
    try:
        tracking.set_goal(_TRACKING_TOPIC, "daily_budget_usd", config.CODING_DAILY_BUDGET_USD,
                           unit="usd", label="Tageslimit Coding-Engine")
        tracking.set_goal(_TRACKING_TOPIC, "task_budget_usd", config.CODING_TASK_BUDGET_USD,
                           unit="usd", label="Task-Limit Coding-Engine")
    except Exception as e:
        print(f"[coding_engine] Goal-Sync Fehler: {e}", flush=True)


# ── Öffentliche API ──────────────────────────────────────────────────────────

def start_task(instruction: str, high_power: bool = False) -> str:
    """Von tools.execute() aufgerufen. Kehrt sofort zurück, Task läuft im Hintergrund.

    high_power=True nutzt das teurere, stärkere Modell (nur wenn Simon das für
    diesen Task explizit verlangt hat) — Default ist das günstigere Modell.
    """
    global _task_running

    spent_today = _today_spend()
    if spent_today >= config.CODING_DAILY_BUDGET_USD:
        return (
            f"Tageslimit der Coding-Engine erreicht (${spent_today:.2f} von ${config.CODING_DAILY_BUDGET_USD:.2f}) — "
            "heute keine weiteren Coding-Tasks mehr, morgen wieder verfügbar."
        )

    with _task_lock:
        if _task_running:
            return "Es läuft bereits ein Coding-Task — ich melde mich, sobald der fertig ist, bevor ich einen neuen starte."
        _task_running = True

    threading.Thread(target=_run_task_thread, args=(instruction, high_power), daemon=True).start()
    model_note = " (mit mehr Power, teureres Modell)" if high_power else ""
    return f"Ich fange im Hintergrund an{model_note}. Ich melde mich per Notification, wenn ich fertig bin oder eine Freigabe brauche."


def resolve_approval(approval_id: str, approved: bool) -> None:
    """Von server.py bei CODING_APPROVAL_RESPONSE aufgerufen."""
    with _approval_lock:
        _pending_decisions[approval_id] = bool(approved)
        event = _pending_events.get(approval_id)
    if event:
        event.set()
    else:
        print(f"[coding_engine] Freigabe-Antwort ohne passenden Task: {approval_id}", flush=True)


# ── Task-Ausführung ───────────────────────────────────────────────────────────

def _run_task_thread(instruction: str, high_power: bool = False) -> None:
    global _task_running
    try:
        asyncio.run(_run_task(instruction, high_power))
    except Exception as e:
        print(f"[coding_engine] Task-Fehler: {e}", flush=True)
        _notify(f"Coding-Task fehlgeschlagen: {e}", priority="high")
    finally:
        with _task_lock:
            _task_running = False
        # Sicherheitsnetz: falls _run_task vor ihrem eigenen _set_status(active=False)
        # abbricht (z.B. Exception vor Worktree-Erstellung), Status trotzdem zurücksetzen.
        _set_status(active=False)


async def _run_task(instruction: str, high_power: bool = False) -> None:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher, ResultMessage

    branch = f"jarvis/auto-{int(time.time())}"
    worktree_path = _create_worktree(branch)
    if worktree_path is None:
        _notify("Konnte keinen eigenen Worktree/Branch anlegen — Task abgebrochen.", priority="high")
        return

    model = config.CODING_ENGINE_MODEL_HIGH if high_power else config.CODING_ENGINE_MODEL
    _notify(f"[JARVIS Code] Starte auf Branch {branch} ({model}): {instruction[:120]}", priority="normal")
    _set_status(
        active=True, branch=branch, model=model, instruction=instruction[:200],
        started_at=datetime.now().isoformat(), last_action="Gestartet",
    )

    original_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = config.CODING_ENGINE_API_KEY
    result_message = None
    try:
        options = ClaudeAgentOptions(
            cwd=str(worktree_path),
            model=model,
            can_use_tool=_make_can_use_tool(worktree_path),
            # Minimaler eigener System-Prompt statt des großen eingebauten
            # Claude-Code-Presets (Slash-Commands, Skills, Subagents-Anleitung
            # etc.) — das allein war beim "sag hallo"-Test schon ~22K Tokens
            # Cache-Write. Für einen engen, automatisierten Einzel-Task nicht nötig.
            system_prompt=_SYSTEM_PROMPT,
            # Kein automatisches Laden von .mcp.json (verbindet sonst unnötig
            # mit dem JARVIS-MCP-Server samt dessen "ruf proaktiv
            # jarvis_get_coding_context() auf"-Anweisung) und keine
            # .claude/settings*.json-Auto-Discovery.
            strict_mcp_config=True,
            mcp_servers={},
            setting_sources=[],
            # System-Prompt + Tool-Schemas sind jetzt fix und identisch über alle
            # Tasks hinweg — 1h-Cache-TTL statt 5min erhöht die Chance, dass ein
            # zweiter Task in derselben Stunde von diesem (jetzt kleinen) Prefix
            # als günstiger Cache-Read statt vollem Neu-Schreiben profitiert.
            env={"ENABLE_PROMPT_CACHING_1H": "1"},
            # WICHTIG: Read/Write/Edit/Bash/Glob/Grep bewusst NICHT in allowed_tools —
            # ein allowed_tools-Eintrag genehmigt ein Tool automatisch, BEVOR
            # can_use_tool gefragt wird (CanUseToolShadowedWarning). Sie bleiben
            # trotzdem verfügbar und fallen mangels Eintrag auf can_use_tool zurück,
            # das dann pro Aufruf über _is_risky() entscheidet.
            # disallowed_tools entfernt außerdem deren Schemas komplett aus dem,
            # was an die API geschickt wird — spart Tokens bei jedem einzelnen Call.
            disallowed_tools=[
                "Task", "CronCreate", "CronDelete", "CronList", "DesignSync",
                "EnterWorktree", "ExitWorktree", "NotebookEdit", "PushNotification",
                "ReportFindings", "ScheduleWakeup", "SendMessage", "Skill",
                "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate",
                "ToolSearch", "WebFetch", "WebSearch", "Workflow",
            ],
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|Bash", hooks=[_progress_hook])]},
            permission_mode="default",
            max_budget_usd=config.CODING_TASK_BUDGET_USD,
            max_turns=_MAX_TURNS_SAFETY,
        )
        try:
            # query() erfordert bei can_use_tool einen AsyncIterable-Prompt statt eines
            # simplen Strings ("requires streaming mode") — ClaudeSDKClient hat diese
            # Einschränkung nicht und ist für einzelne Tasks mit Freigabe-Callback die
            # von Anthropic empfohlene, einfachere Variante.
            async with ClaudeSDKClient(options=options) as client:
                await client.query(_build_prompt(instruction))
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        result_message = message
        except Exception as e:
            print(f"[coding_engine] Task-Abbruch: {e}", flush=True)
    finally:
        if original_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = original_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    committed = _finalize_commit(worktree_path, branch, instruction)

    # total_cost_usd ist eine Client-seitige Schätzung des SDK, keine autoritative
    # Abrechnung (siehe Agent-SDK-Doku "Track cost and usage") — für die exakte
    # Rechnung zählt die Anthropic Console, hier nur als Budget-Näherung genutzt.
    cost = (result_message.total_cost_usd or 0.0) if result_message else 0.0
    today_total = _record_spend(cost, branch, instruction)
    summary = (result_message.result[:400] if result_message and result_message.result else "(keine Zusammenfassung)")
    error_note = " ⚠️ mit Fehler/Abbruch beendet" if (result_message and result_message.is_error) else ""

    if committed:
        location_note = f"Branch {branch} (Worktree: {worktree_path})"
    else:
        location_note = f"Branch {branch} — keine Änderungen, Worktree wieder entfernt"

    _notify(
        f"[JARVIS Code] Fertig — {location_note}{error_note} — ${cost:.2f} "
        f"(heute ${today_total:.2f} von ${config.CODING_DAILY_BUDGET_USD:.2f}): {summary}",
        priority="normal", expires_in_min=1440,
    )
    _set_status(active=False, last_action="Fertig" if not error_note else "Fehler/Abbruch")


def _build_prompt(instruction: str) -> str:
    return (
        "Du bist JARVIS' eigene Coding-Engine und arbeitest in deinem eigenen Server-Repo, "
        "auf einem bereits ausgecheckten eigenen Branch (niemals main). "
        f"Aufgabe: {instruction}"
    )


# ── Git (immer über einen isolierten Worktree, nie im Haupt-Checkout) ─────────

def _create_worktree(branch: str) -> Path | None:
    """Legt einen neuen git-worktree an, ausgehend vom letzten Commit auf main —
    unabhängig davon, was gerade uncommittet im Haupt-Checkout (WORKSPACE_ROOT)
    liegt. Wechselt NICHT den Branch von WORKSPACE_ROOT selbst."""
    worktree_path = _WORKTREE_BASE / branch
    try:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path), "main"],
            cwd=str(WORKSPACE_ROOT), check=True, capture_output=True, text=True,
        )
        return worktree_path
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Worktree-Fehler: {e.stderr}", flush=True)
        return None


def _finalize_commit(worktree_path: Path, branch: str, instruction: str) -> bool:
    """Committet Änderungen im Worktree. Räumt den Worktree auf wenn es nichts
    zu committen gab. Gibt zurück ob etwas committet wurde."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(worktree_path), capture_output=True, text=True, check=True,
        )
        if not status.stdout.strip():
            _remove_worktree(worktree_path, branch, delete_branch=True)
            return False

        subprocess.run(["git", "add", "-A"], cwd=str(worktree_path), check=True)
        subprocess.run(
            ["git", *_GIT_AUTHOR, "commit", "-m", f"JARVIS: {instruction[:72]}"],
            cwd=str(worktree_path), check=True, capture_output=True, text=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Commit-Fehler: {e.stderr}", flush=True)
        return False


def _remove_worktree(worktree_path: Path, branch: str, delete_branch: bool = False) -> None:
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(WORKSPACE_ROOT), check=True, capture_output=True, text=True,
        )
        if delete_branch:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(WORKSPACE_ROOT), check=True, capture_output=True, text=True,
            )
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Worktree-Cleanup Fehler: {e.stderr}", flush=True)


# ── Freigabe / Eskalation ──────────────────────────────────────────────────────

def _make_can_use_tool(workspace_root: Path):
    """Baut can_use_tool für einen konkreten Task, gebunden an dessen Worktree —
    'außerhalb des Workspace' bedeutet außerhalb DIESES Worktrees, nicht des
    Haupt-Checkouts."""
    async def can_use_tool(tool_name, input_data, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if config.CODING_MANUAL_MODE or _is_risky(tool_name, input_data, workspace_root):
            approved = await _escalate(tool_name, input_data)
            if approved:
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(message="Simon hat diese Aktion nicht freigegeben.", interrupt=False)

        return PermissionResultAllow(updated_input=input_data)

    return can_use_tool


def _is_risky(tool_name: str, input_data: dict, workspace_root: Path) -> bool:
    if tool_name in ("Write", "Edit"):
        raw_path = input_data.get("file_path", "")
        if not raw_path:
            return False
        try:
            resolved = Path(raw_path)
            if not resolved.is_absolute():
                resolved = workspace_root / resolved
            resolved = resolved.resolve()
        except Exception:
            return True
        if not resolved.is_relative_to(workspace_root):
            return True
        return any(p in resolved.name for p in _SECRET_PATTERNS)

    if tool_name == "Bash":
        command = (input_data.get("command") or "").lower()
        return any(p in command for p in _DESTRUCTIVE_PATTERNS) or any(p in command for p in _SECRET_PATTERNS)

    return False


async def _escalate(tool_name: str, input_data: dict) -> bool:
    approval_id = str(uuid.uuid4())
    event = threading.Event()
    with _approval_lock:
        _pending_events[approval_id] = event

    summary = _describe_action(tool_name, input_data)
    _push_approval_request(approval_id, summary)

    loop = asyncio.get_running_loop()
    got_response = await loop.run_in_executor(None, event.wait, _APPROVAL_TIMEOUT_SEC)

    with _approval_lock:
        decision = _pending_decisions.pop(approval_id, False)
        _pending_events.pop(approval_id, None)

    if not got_response:
        _notify(f"Keine Antwort auf Freigabe-Anfrage ({summary}) — abgelehnt.", priority="high")
        return False
    return decision


def _describe_action(tool_name: str, input_data: dict) -> str:
    if tool_name in ("Write", "Edit"):
        return f"{tool_name}: {input_data.get('file_path', '?')}"
    if tool_name == "Bash":
        return f"Bash: {(input_data.get('command') or '?')[:200]}"
    return f"{tool_name}: {json.dumps(input_data, ensure_ascii=False)[:200]}"


def _push_approval_request(approval_id: str, summary: str) -> None:
    if not _manager:
        return
    payload = {
        "type": P.CODING_APPROVAL_REQUEST,
        "id": approval_id,
        "text": f"JARVIS möchte: {summary} — freigeben?",
    }
    for cb, _mode in _manager.get_dashboard_event_callbacks():
        try:
            cb(payload)
        except Exception as e:
            print(f"[coding_engine] Freigabe-Push-Fehler: {e}", flush=True)
    print(f"[coding_engine] Freigabe angefragt: {summary}", flush=True)


# ── Fortschritt ────────────────────────────────────────────────────────────────
# Eigener Broadcast-Kanal für Live-Status (jarvis-web) — bewusst NICHT über den
# NotificationDispatcher (der erlaubt nur 3 Pushes/Stunde global, s.u. bei
# _progress_hook). coding_task_status geht direkt an alle Dashboard-Clients.

def _set_status(**fields) -> None:
    with _status_lock:
        _current_status.update(fields)
        snapshot = dict(_current_status)
    _push_task_status(snapshot)


def get_task_status() -> dict:
    """Für data_request 'coding_task_status' — initialer Stand beim Seitenaufruf."""
    with _status_lock:
        return dict(_current_status)


def _push_task_status(status: dict) -> None:
    if not _manager:
        return
    payload = {"type": P.CODING_TASK_STATUS, **status}
    for cb, _mode in _manager.get_dashboard_event_callbacks():
        try:
            cb(payload)
        except Exception as e:
            print(f"[coding_engine] Status-Push-Fehler: {e}", flush=True)


async def _progress_hook(input_data, tool_use_id, context):
    # Nur ins Server-Log, NICHT über den Notification-Dispatcher — der erlaubt
    # global nur 3 Pushes/Stunde (geteilt mit Kalender/Email-Remindern etc.).
    # Bei mehreren Tool-Calls pro Task würde das die wichtige Abschluss-
    # Notification verdrängen. Für Debugging reicht journalctl.
    tool_name = input_data.get("tool_name", "?")
    tool_input = input_data.get("tool_input", {}) or {}
    if tool_name in ("Write", "Edit"):
        detail = tool_input.get("file_path", "?")
    elif tool_name == "Bash":
        detail = (tool_input.get("command") or "?")[:80]
    else:
        detail = tool_name
    print(f"[coding_engine] {tool_name}: {detail}", flush=True)
    _set_status(last_action=f"{tool_name}: {detail}")
    return {}


def _notify(text: str, priority: str = "normal", expires_in_min: int = 60) -> None:
    if _dispatcher:
        _dispatcher.notify(text, channels=["dashboard"], priority=priority, expires_in_min=expires_in_min)
    else:
        print(f"[coding_engine] (kein Dispatcher) {text}", flush=True)


# ── Kosten-Tracking (über tracking.py — ein Log-Eintrag pro Task) ──────────────

def _today_spend() -> float:
    today = date.today().isoformat()
    logs = tracking.get_logs(_TRACKING_TOPIC, key="cost_usd", since_date=today, limit=1000)
    return round(sum((l["value"] or 0.0) for l in logs if l["date"] == today), 4)


def _record_spend(cost_usd: float, branch: str, instruction: str) -> float:
    """Loggt die Kosten eines Tasks, gibt den neuen Tages-Gesamtwert zurück."""
    tracking.add_log(
        _TRACKING_TOPIC, key="cost_usd", value=round(max(cost_usd, 0.0), 4),
        unit="usd", notes=f"{branch}: {instruction[:100]}",
    )
    return _today_spend()


def get_usage_summary(days: int = 14) -> dict:
    """Für die Kosten-Grafik in jarvis-web (data_request 'coding_engine_usage')."""
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    logs = tracking.get_logs(_TRACKING_TOPIC, key="cost_usd", since_date=since, limit=1000)
    daily: dict[str, float] = {}
    for entry in logs:
        d = entry["date"]
        daily[d] = round(daily.get(d, 0.0) + (entry["value"] or 0.0), 4)

    series = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        series.append({"date": d, "cost_usd": daily.get(d, 0.0)})

    return {
        "daily": series,
        "today_usd": daily.get(date.today().isoformat(), 0.0),
        "task_limit_usd": config.CODING_TASK_BUDGET_USD,
        "daily_limit_usd": config.CODING_DAILY_BUDGET_USD,
    }
