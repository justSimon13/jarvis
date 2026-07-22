"""
JARVIS Coding Engine — JARVIS entwickelt sich selbst, über das Claude Agent SDK.

Läuft als eigener Hintergrund-Thread pro Task (analog zu services/proactive.py),
nie auf dem asyncio-Haupt-Loop von server.py. Arbeitet in einem eigenen
`git worktree` (eigener Branch, eigenes Arbeitsverzeichnis) — NICHT im
gemeinsam genutzten Haupt-Checkout, damit Simons/Claude Codes eigene, noch
uncommittete Arbeit dort nie berührt wird und der Branch-Wechsel nicht den
für alle sichtbaren Checkout umschaltet.

Ziel-Repo pro Task: standardmäßig WORKSPACE_ROOT (dieses j.a.r.v.i.s.-Server-
Repo selbst), optional eines der von create_project() unter config.PROJECTS_ROOT
angelegten Projekte (Parameter `project` bei start_task()) — JARVIS kann also
nur im eigenen Server-Repo oder in Projekten, die es selbst (mit Freigabe)
angelegt hat, Code schreiben, nirgendwo sonst auf dem Server.

Freigabe-Fluss: can_use_tool() blockiert (in einem Executor-Thread, nicht auf
dem Coding-Engine-eigenen Event-Loop) bis server.py per resolve_approval()
eine Antwort auf CODING_APPROVAL_REQUEST liefert. Per Task abschaltbar
(`auto_mode=True`) wenn Simon das für diesen einen Task explizit so verlangt —
dann läuft der komplette Task ohne jede Rückfrage durch (2026-07-22:
"ich will ihm auch sagen können, dass er das im auto mode einfach
runter programmiert - ohne meine Bestätigung"). Landet trotzdem nie direkt
auf main — der PR danach bleibt die letzte Kontrollinstanz.
"""
import asyncio
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
def _build_system_prompt(project_root: Path) -> str:
    if project_root == WORKSPACE_ROOT:
        repo_note = (
            "Du arbeitest im j.a.r.v.i.s.-Server-Repo (Python asyncio WebSocket-Server). "
            "Konventionen: Print-Ausgaben mit Prefix [modulname] und flush=True; Kommentare "
            "Deutsch oder Englisch; Services sind isoliert, keine Cross-Service-Imports."
        )
    else:
        repo_note = f"Du arbeitest im Projekt '{project_root.name}' unter {project_root}."
    return (
        "Du bist JARVIS' eigene Coding-Engine, ein autonomer Hintergrund-Task ohne "
        f"Live-Rückfragemöglichkeit an Simon. {repo_note} Du bist bereits auf einem "
        "eigenen Git-Branch (niemals main) ausgecheckt. Halte Änderungen minimal und "
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
    # Frisch schreiben statt einer eventuell veralteten Datei von vor dem letzten
    # Neustart vertrauen — direkt nach dem Start sind garantiert 0 Clients
    # verbunden und kein Coding-Task aktiv.
    refresh_idle_status()


# ── Öffentliche API ──────────────────────────────────────────────────────────

def start_task(instruction: str, high_power: bool = False, auto_mode: bool = False, project: str | None = None) -> str:
    """Von tools.execute() aufgerufen. Kehrt sofort zurück, Task läuft im Hintergrund.

    high_power=True nutzt das teurere, stärkere Modell (nur wenn Simon das für
    diesen Task explizit verlangt hat) — Default ist das günstigere Modell.

    project=None (Default) = dieses j.a.r.v.i.s.-Server-Repo selbst. Sonst Name
    eines zuvor mit create_project() angelegten Projekts unter config.PROJECTS_ROOT.

    auto_mode=True überspringt für DIESEN Task jede Freigabe-Rückfrage
    (Write/Edit/Bash außerhalb des Sandbox-Worktrees, riskante Bash-Befehle,
    Secret-Dateien) — nur setzen wenn Simon das für diesen Task ausdrücklich
    so verlangt hat. Der PR am Ende bleibt trotzdem die letzte Kontrollinstanz,
    nichts landet je direkt auf main.
    """
    global _task_running

    project_root = WORKSPACE_ROOT
    if project:
        candidate = config.PROJECTS_ROOT / project
        if not (candidate / ".git").is_dir():
            return f"Projekt '{project}' nicht gefunden unter {config.PROJECTS_ROOT} — erst mit create_project anlegen."
        project_root = candidate

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

    threading.Thread(
        target=_run_task_thread, args=(instruction, high_power, auto_mode, project_root), daemon=True,
    ).start()
    model_note = " (mit mehr Power, teureres Modell)" if high_power else ""
    auto_note = " im Auto-Modus, ohne Rückfragen" if auto_mode else ""
    project_note = f" am Projekt '{project}'" if project else ""
    return (
        f"Ich fange im Hintergrund an{project_note}{model_note}{auto_note}. "
        "Ich melde mich per Notification, wenn ich fertig bin"
        f"{' oder eine Freigabe brauche' if not auto_mode else ''}."
    )


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

def _run_task_thread(instruction: str, high_power: bool = False, auto_mode: bool = False, project_root: Path | None = None) -> None:
    global _task_running
    try:
        asyncio.run(_run_task(instruction, high_power, auto_mode, project_root))
    except Exception as e:
        print(f"[coding_engine] Task-Fehler: {e}", flush=True)
        _notify(f"Coding-Task fehlgeschlagen: {e}", priority="high")
    finally:
        with _task_lock:
            _task_running = False
        # Sicherheitsnetz: falls _run_task vor ihrem eigenen _set_status(active=False)
        # abbricht (z.B. Exception vor Worktree-Erstellung), Status trotzdem zurücksetzen.
        _set_status(active=False)


async def _run_task(instruction: str, high_power: bool = False, auto_mode: bool = False, project_root: Path | None = None) -> None:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher, ResultMessage

    project_root = project_root or WORKSPACE_ROOT
    branch = f"jarvis/auto-{int(time.time())}"
    worktree_path = _create_worktree(project_root, branch)
    if worktree_path is None:
        _notify("Konnte keinen eigenen Worktree/Branch anlegen — Task abgebrochen.", priority="high")
        return

    model = config.CODING_ENGINE_MODEL_HIGH if high_power else config.CODING_ENGINE_MODEL
    project_note = f" [Projekt: {project_root.name}]" if project_root != WORKSPACE_ROOT else ""
    auto_note = " [Auto-Modus]" if auto_mode else ""
    _notify(f"[JARVIS Code]{project_note}{auto_note} Starte auf Branch {branch} ({model}): {instruction[:120]}", priority="normal")
    _set_status(
        active=True, branch=branch, model=model, instruction=instruction[:200],
        project=project_root.name if project_root != WORKSPACE_ROOT else None, auto_mode=auto_mode,
        started_at=datetime.now().isoformat(), last_action="Gestartet",
    )

    original_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = config.CODING_ENGINE_API_KEY
    result_message = None
    try:
        options = ClaudeAgentOptions(
            cwd=str(worktree_path),
            model=model,
            can_use_tool=_make_can_use_tool(worktree_path, auto_mode),
            # Minimaler eigener System-Prompt statt des großen eingebauten
            # Claude-Code-Presets (Slash-Commands, Skills, Subagents-Anleitung
            # etc.) — das allein war beim "sag hallo"-Test schon ~22K Tokens
            # Cache-Write. Für einen engen, automatisierten Einzel-Task nicht nötig.
            system_prompt=_build_system_prompt(project_root),
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
                await client.query(_build_prompt(instruction, project_root))
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

    committed = _finalize_commit(project_root, worktree_path, branch, instruction)

    # total_cost_usd ist eine Client-seitige Schätzung des SDK, keine autoritative
    # Abrechnung (siehe Agent-SDK-Doku "Track cost and usage") — für die exakte
    # Rechnung zählt die Anthropic Console, hier nur als Budget-Näherung genutzt.
    cost = (result_message.total_cost_usd or 0.0) if result_message else 0.0
    today_total = _record_spend(cost, branch, instruction)
    summary = (result_message.result[:400] if result_message and result_message.result else "(keine Zusammenfassung)")
    error_note = " ⚠️ mit Fehler/Abbruch beendet" if (result_message and result_message.is_error) else ""

    pr_url = None
    if committed:
        repo_slug = _repo_slug_for(project_root)
        pr_url = _create_pull_request(repo_slug, branch, instruction, worktree_path)
        if pr_url:
            location_note = f"PR erstellt: {pr_url}"
        else:
            location_note = f"Branch {branch} (Worktree: {worktree_path}) — kein PR (siehe Server-Log)"
    else:
        location_note = f"Branch {branch} — keine Änderungen, Worktree wieder entfernt"

    _notify(
        f"[JARVIS Code]{project_note}{auto_note} Fertig — {location_note}{error_note} — ${cost:.2f} "
        f"(heute ${today_total:.2f} von ${config.CODING_DAILY_BUDGET_USD:.2f}): {summary}",
        # priority="high": bleibt stehen bis manuell weggeklickt (jarvis-web dismisst
        # alles außer "high" automatisch nach 8s) — eine Fertig-Meldung, die man
        # verpasst, ist genau so schlimm wie gar keine (2026-07-22 gemeldet).
        priority="high", expires_in_min=1440,
    )
    _set_status(
        active=False, last_action="Fertig" if not error_note else "Fehler/Abbruch",
        pr_url=pr_url,
    )


def _build_prompt(instruction: str, project_root: Path) -> str:
    where = "deinem eigenen Server-Repo" if project_root == WORKSPACE_ROOT else f"dem Projekt '{project_root.name}'"
    return (
        f"Du bist JARVIS' eigene Coding-Engine und arbeitest in {where}, "
        "auf einem bereits ausgecheckten eigenen Branch (niemals main). "
        f"Aufgabe: {instruction}"
    )


def _repo_slug_for(project_root: Path) -> str:
    """'owner/repo' für die GitHub-API — alle create_project()-Repos liegen unter
    demselben Owner wie das j.a.r.v.i.s.-Repo selbst (Simons eigener Account)."""
    if project_root == WORKSPACE_ROOT:
        return config.GITHUB_REPO
    owner = config.GITHUB_REPO.split("/")[0]
    return f"{owner}/{project_root.name}"


# ── Git (immer über einen isolierten Worktree, nie im Haupt-Checkout) ─────────

def _sync_main(project_root: Path) -> str:
    """Holt --ff-only den aktuellen main-Stand von origin in project_root, wenn
    das sicher möglich ist. Rührt project_root NIE an wenn es gerade nicht
    sauber oder nicht auf main ist (z.B. weil Simon oder Claude Code über den
    SMB-Mount mittendrin sind) — dann einfach der vorhandene Stand, statt
    irgendwas zu riskieren. Gibt eine kurze, für Menschen lesbare
    Ergebnismeldung zurück (genutzt sowohl von _sync_main_before_task als
    stiller Vorab-Schritt als auch von sync_project() als direkte Tool-Antwort
    an Simon)."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, check=True,
        ).stdout.strip()
        if branch != "main":
            return f"Checkout ist gerade nicht auf main (sondern {branch}) — kein Pull versucht."

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root), capture_output=True, text=True, check=True,
        )
        if status.stdout.strip():
            return "Checkout hat gerade uncommittete Änderungen — kein Pull versucht, um nichts zu riskieren."

        subprocess.run(["git", "fetch", "origin", "main", "--quiet"], cwd=str(project_root), check=True, capture_output=True, text=True)
        local_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project_root), capture_output=True, text=True, check=True).stdout.strip()
        remote_rev = subprocess.run(["git", "rev-parse", "origin/main"], cwd=str(project_root), capture_output=True, text=True, check=True).stdout.strip()
        if local_rev == remote_rev:
            return f"Bereits aktuell (auf {local_rev[:7]})."

        subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(project_root), check=True, capture_output=True, text=True,
        )
        return f"Gepullt: {local_rev[:7]} → {remote_rev[:7]}."
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() if e.stderr else str(e)
        return f"Pull fehlgeschlagen: {detail[:200]}"


def _sync_main_before_task(project_root: Path) -> None:
    """Holt vor einem neuen Task den aktuellen main-Stand von origin, damit ein
    frisch gemergter PR (egal ob von Simon selbst oder aus einem vorherigen
    JARVIS-Task) nicht verpasst wird — sonst würde der neue Task-Worktree vom
    alten main abzweigen und Simons gerade gemergte Änderung ignorieren
    (2026-07-22: 'jarvis coded, ich merge PR, danach soll er mit aktuellem
    main weiter coden können'). Rein informativ — läuft still im Server-Log,
    ein fehlgeschlagener Pull bricht den Task nicht ab, er läuft dann einfach
    mit dem vorhandenen Stand weiter."""
    result = _sync_main(project_root)
    print(f"[coding_engine] {project_root.name}: {result}", flush=True)


def sync_project(project: str | None = None) -> str:
    """Von tools.execute() aufgerufen ('sync_project'). Direkter, kostenloser
    Pull-Befehl ohne Coding-Task/LLM-Sub-Session — Simon wollte JARVIS im Chat
    direkt 'pull jetzt' sagen können (2026-07-22), ohne dafür Budget für einen
    ganzen Coding-Task zu verbrennen (der bräuchte eine Agent-SDK-Session nur
    um am Ende git pull auszuführen — das übernimmt _sync_main_before_task
    für echte Coding-Tasks ohnehin schon automatisch). Läuft synchron, kein
    Freigabe-Dialog nötig — git fetch + --ff-only-pull ist read-mostly und
    rührt nie etwas an, das Checkout-Sauberkeit riskieren würde."""
    project_root = WORKSPACE_ROOT
    if project:
        candidate = config.PROJECTS_ROOT / project
        if not (candidate / ".git").is_dir():
            return f"Projekt '{project}' nicht gefunden unter {config.PROJECTS_ROOT}."
        project_root = candidate

    label = "j.a.r.v.i.s.-Server-Repo" if project_root == WORKSPACE_ROOT else f"Projekt '{project}'"
    return f"{label}: {_sync_main(project_root)}"


def _create_worktree(project_root: Path, branch: str) -> Path | None:
    """Legt einen neuen git-worktree an, ausgehend vom letzten Commit auf main —
    unabhängig davon, was gerade uncommittet im Haupt-Checkout (project_root)
    liegt. Wechselt NICHT den Branch von project_root selbst."""
    _sync_main_before_task(project_root)
    worktree_path = _WORKTREE_BASE / branch
    try:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path), "main"],
            cwd=str(project_root), check=True, capture_output=True, text=True,
        )
        return worktree_path
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Worktree-Fehler: {e.stderr}", flush=True)
        return None


def _finalize_commit(project_root: Path, worktree_path: Path, branch: str, instruction: str) -> bool:
    """Committet Änderungen im Worktree. Räumt den Worktree auf wenn es nichts
    zu committen gab. Gibt zurück ob etwas committet wurde."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(worktree_path), capture_output=True, text=True, check=True,
        )
        if not status.stdout.strip():
            _remove_worktree(project_root, worktree_path, branch, delete_branch=True)
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


def _remove_worktree(project_root: Path, worktree_path: Path, branch: str, delete_branch: bool = False) -> None:
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(project_root), check=True, capture_output=True, text=True,
        )
        if delete_branch:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(project_root), check=True, capture_output=True, text=True,
            )
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Worktree-Cleanup Fehler: {e.stderr}", flush=True)


def _create_pull_request(repo_slug: str, branch: str, instruction: str, worktree_path: Path) -> str | None:
    """Pusht den Branch und legt einen echten GitHub-PR an (im durch repo_slug
    angegebenen Repo — dem j.a.r.v.i.s.-Repo selbst oder einem von create_project
    angelegten Projekt). Committet wird weiterhin ohne Rückfrage (wie bisher) —
    aber damit die Arbeit nicht einfach auf einem lokalen Branch liegen bleibt,
    den niemand mehr anschaut, braucht der Merge nach main jetzt Simons bewusste
    Aktion (PR annehmen). Push nutzt GITHUB_TOKEN direkt in der URL statt sich
    auf eine vorhandene Git-Credential-Konfiguration zu verlassen. Gibt die
    PR-URL zurück, oder None wenn kein Token gesetzt ist oder Push/PR fehlschlägt
    (Ergebnis bleibt dann trotzdem auf dem Branch erhalten, nur ohne PR — kein
    Datenverlust)."""
    if not config.GITHUB_TOKEN:
        print("[coding_engine] Kein GITHUB_TOKEN gesetzt — kein PR, Branch bleibt lokal.", flush=True)
        return None

    push_url = f"https://x-access-token:{config.GITHUB_TOKEN}@github.com/{repo_slug}.git"
    try:
        subprocess.run(
            ["git", "push", push_url, f"{branch}:{branch}"],
            cwd=str(worktree_path), check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Push-Fehler: {e.stderr}", flush=True)
        return None

    body = json.dumps({
        "title": f"JARVIS: {instruction[:72]}",
        "head": branch,
        "base": "main",
        "body": f"Automatisch von JARVIS' Coding-Engine erstellt.\n\n**Aufgabe:**\n{instruction}",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_slug}/pulls",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("html_url")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"[coding_engine] PR-Erstellung fehlgeschlagen ({e.code}): {detail[:300]}", flush=True)
        return None
    except Exception as e:
        print(f"[coding_engine] PR-Erstellung Fehler: {e}", flush=True)
        return None


# Muss mit einem Buchstaben/einer Zahl beginnen — sonst wären Namen wie ".."
# oder "." gültig (nur erlaubte Zeichen, aber Verzeichnis-Traversal-Risiko für
# den lokalen Checkout-Pfad unter config.PROJECTS_ROOT).
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def create_project(name: str, description: str = "", private: bool = True) -> str:
    """Von tools.execute() aufgerufen ('create_project'). Kehrt SOFORT zurück
    und macht die eigentliche Arbeit (Freigabe abwarten, dann anlegen) in einem
    Hintergrund-Thread — analog zu start_task(). Nötig weil tools.execute() hier
    synchron innerhalb von pipeline.process_text() läuft, das server.py per
    run_in_executor auf genau der Verbindung ausführt, über die später auch die
    Freigabe-Antwort reinkommt: würde hier blockierend auf die Freigabe gewartet,
    könnte die Antwort nie ankommen (Deadlock) — anders als bei den Write/Edit/
    Bash-Eskalationen der Coding-Engine, die in ihrem eigenen, komplett
    losgelösten Task-Thread laufen."""
    if not config.GITHUB_TOKEN:
        return "Kein GITHUB_TOKEN konfiguriert — ich kann kein Projekt anlegen."

    if not name or not _REPO_NAME_RE.match(name):
        return f"Ungültiger Projekt-Name '{name}' — erlaubt sind nur Buchstaben, Zahlen, Punkt, Unterstrich, Bindestrich."

    if (config.PROJECTS_ROOT / name).exists():
        return f"Ordner '{name}' existiert unter {config.PROJECTS_ROOT} bereits — anderen Namen wählen."

    threading.Thread(target=_create_project_thread, args=(name, description, private), daemon=True).start()
    return "Ich frage kurz im Dashboard nach, ob ich das Projekt anlegen darf — Ergebnis kommt per Notification."


def _create_project_thread(name: str, description: str, private: bool) -> None:
    """Fragt IMMER zuerst Simons Freigabe über denselben Dashboard-Modal-Flow wie
    riskante Coding-Task-Aktionen — ein neues Projekt ist nach außen sichtbar
    (GitHub, bei public) und nicht mit einem einfachen Undo rückgängig zu
    machen (2026-07-22: 'ich will jarvis sagen können, erstell mir Projekt xy').
    Legt danach GitHub-Repo + lokalen Checkout unter config.PROJECTS_ROOT an —
    einem festen, begrenzten Ordner, damit JARVIS nirgendwo sonst auf dem
    Server neue Verzeichnisse anlegen kann."""
    local_path = config.PROJECTS_ROOT / name
    visibility = "privat" if private else "öffentlich"
    summary = f"Neues Projekt anlegen: {name} ({visibility})"
    detail = (
        f"Name: {name}\nSichtbarkeit: {visibility}\nBeschreibung: {description or '(keine)'}\n"
        f"Lokal auf dem Server: {local_path}"
    )
    if not _request_approval_sync("CreateProject", summary, detail):
        _notify(f"Projekt-Erstellung '{name}' nicht freigegeben.", priority="normal")
        return

    repo = _github_create_repo(name, description, private)
    if repo is None:
        _notify(f"Projekt-Erstellung '{name}' fehlgeschlagen — Repo konnte nicht angelegt werden (siehe Server-Log).", priority="high")
        return

    html_url = repo.get("html_url", "?")
    clone_url = repo.get("clone_url")
    if not clone_url:
        _notify(f"Repo '{name}' erstellt ({html_url}), aber keine clone_url erhalten — lokaler Checkout übersprungen.", priority="high")
        return

    config.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    clone_url_with_token = clone_url.replace("https://", f"https://x-access-token:{config.GITHUB_TOKEN}@")
    try:
        subprocess.run(
            ["git", "clone", clone_url_with_token, str(local_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        # git speichert die Clone-URL 1:1 als "origin" in .git/config — mit Token
        # eingebettet läge der PAT sonst dauerhaft im Klartext auf der Platte.
        # Push/PR bauen sich die Token-URL ohnehin bei Bedarf frisch (s. _create_pull_request),
        # der gespeicherte Remote braucht also keine Credentials.
        subprocess.run(
            ["git", "remote", "set-url", "origin", clone_url],
            cwd=str(local_path), check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[coding_engine] Lokaler Checkout fehlgeschlagen: {e.stderr}", flush=True)
        _notify(f"Repo '{name}' erstellt ({html_url}), aber lokaler Checkout auf dem Server fehlgeschlagen (siehe Server-Log).", priority="high")
        return
    except Exception as e:
        print(f"[coding_engine] Lokaler Checkout Fehler: {e}", flush=True)
        _notify(f"Repo '{name}' erstellt ({html_url}), aber lokaler Checkout auf dem Server fehlgeschlagen: {e}", priority="high")
        return

    _notify(f"[JARVIS] Projekt '{name}' angelegt: {html_url} (lokal: {local_path})", priority="high", expires_in_min=1440)


def _github_create_repo(name: str, description: str, private: bool) -> dict | None:
    """Reine GitHub-API-Erstellung (POST /user/repos), kein lokaler Checkout.
    auto_init=True, damit das Repo sofort einen initialen Commit hat — ein
    komplett leeres Repo ließe sich sonst nicht klonen ('repository is empty')."""
    body = json.dumps({
        "name": name, "description": description, "private": private, "auto_init": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"[coding_engine] Repo-Erstellung fehlgeschlagen ({e.code}): {detail[:300]}", flush=True)
        return None
    except Exception as e:
        print(f"[coding_engine] Repo-Erstellung Fehler: {e}", flush=True)
        return None


# ── Freigabe / Eskalation ──────────────────────────────────────────────────────

def _make_can_use_tool(workspace_root: Path, auto_mode: bool = False):
    """Baut can_use_tool für einen konkreten Task, gebunden an dessen Worktree —
    'außerhalb des Workspace' bedeutet außerhalb DIESES Worktrees, nicht des
    Haupt-Checkouts. auto_mode=True (Simon hat das für diesen Task ausdrücklich
    verlangt) lässt jede Aktion ohne Rückfrage durch, auch riskante — der PR am
    Ende bleibt trotzdem die letzte Kontrollinstanz, nichts landet direkt auf main."""
    async def can_use_tool(tool_name, input_data, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if not auto_mode and (config.CODING_MANUAL_MODE or _is_risky(tool_name, input_data, workspace_root)):
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
    summary = _describe_action(tool_name, input_data)
    detail = _detail_for_action(tool_name, input_data)
    file_path = input_data.get("file_path") if tool_name in ("Write", "Edit") else None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _request_approval_sync, tool_name, summary, detail, file_path)


def _request_approval_sync(
    tool_name: str, summary: str, detail: str, file_path: str | None = None,
    timeout: float = _APPROVAL_TIMEOUT_SEC,
) -> bool:
    """Blockiert den aufrufenden Thread bis Simon per CODING_APPROVAL_RESPONSE
    antwortet (oder Timeout). Gemeinsamer Kern für _escalate() (aus dem
    can_use_tool-Callback der Coding-Engine heraus, dort per run_in_executor
    entblockt) UND für eigene Hintergrund-Threads wie _create_project_thread —
    beide blockieren hier jeweils einen eigenen, von server.py's Event-Loop
    losgelösten Thread, nie den Thread der gerade eine Verbindung bedient."""
    approval_id = str(uuid.uuid4())
    event = threading.Event()
    with _approval_lock:
        _pending_events[approval_id] = event

    _push_approval_request(approval_id, tool_name, summary, detail, file_path)

    got_response = event.wait(timeout)

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


_DETAIL_MAX_CHARS = 4000  # großzügig genug zum wirklich Lesen, aber kein Mega-Payload


def _detail_for_action(tool_name: str, input_data: dict) -> str:
    """Der eigentliche Inhalt, den Simon vor dem Freigeben sehen soll — bisher
    zeigte die Freigabe-Anfrage nur den Dateipfad, nicht was reingeschrieben wird
    (2026-07-22 gemeldet: 'ich will auch sehen was ich akzeptiere')."""
    if tool_name == "Write":
        content = input_data.get("content", "")
        if len(content) > _DETAIL_MAX_CHARS:
            content = content[:_DETAIL_MAX_CHARS] + f"\n… [gekürzt, {len(content)} Zeichen insgesamt]"
        return content or "(leerer Inhalt)"

    if tool_name == "Edit":
        old = input_data.get("old_string", "")
        new = input_data.get("new_string", "")
        detail = f"− ALT:\n{old}\n\n+ NEU:\n{new}"
        if len(detail) > _DETAIL_MAX_CHARS:
            detail = detail[:_DETAIL_MAX_CHARS] + f"\n… [gekürzt, {len(detail)} Zeichen insgesamt]"
        return detail

    if tool_name == "Bash":
        command = input_data.get("command", "")
        if len(command) > _DETAIL_MAX_CHARS:
            command = command[:_DETAIL_MAX_CHARS] + f"\n… [gekürzt, {len(command)} Zeichen insgesamt]"
        return command or "(kein Befehl)"

    return json.dumps(input_data, ensure_ascii=False, indent=2)[:_DETAIL_MAX_CHARS]


def _push_approval_request(approval_id: str, tool_name: str, summary: str, detail: str, file_path: str | None) -> None:
    if not _manager:
        return
    payload = {
        "type": P.CODING_APPROVAL_REQUEST,
        "id": approval_id,
        "text": f"JARVIS möchte: {summary} — freigeben?",
        "tool_name": tool_name,
        "file_path": file_path,
        "detail": detail,
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
    refresh_idle_status()


def get_task_status() -> dict:
    """Für data_request 'coding_task_status' — initialer Stand beim Seitenaufruf."""
    with _status_lock:
        return dict(_current_status)


_IDLE_STATUS_PATH = Path.home() / ".jarvis" / "idle_status.json"


def refresh_idle_status() -> None:
    """Schreibt eine kleine Status-Datei für scripts/auto_update.sh — der Timer
    läuft als eigener Bash-Prozess außerhalb von JARVIS und kann sonst nicht
    wissen, ob gerade jemand verbunden ist oder ein Coding-Task läuft. Ohne das
    würde der Auto-Update-Timer JARVIS auch mitten in einem laufenden Gespräch
    neu starten (2026-07-22: 'dann soll er halt restarten wenn alle Prozesse
    abgeschlossen sind und er für seinen Teil fertig ist'). Von server.py bei
    jedem Client-Connect/-Disconnect aufgerufen, und hier bei jeder
    Coding-Task-Statusänderung."""
    try:
        with _status_lock:
            coding_active = bool(_current_status.get("active"))
        connected = len(_manager.connected) if _manager else 0
        status = {
            "connected_clients": connected,
            "coding_task_active": coding_active,
            "updated_at": datetime.now().isoformat(),
        }
        _IDLE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _IDLE_STATUS_PATH.write_text(json.dumps(status), encoding="utf-8")
    except Exception as e:
        print(f"[coding_engine] Idle-Status-Schreibfehler: {e}", flush=True)


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
