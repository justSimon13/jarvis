from __future__ import annotations
import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config

_DB_PATH = Path.home() / ".jarvis" / "sessions.db"


# ── SQLite I/O ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            title      TEXT,
            transcript TEXT
        )
    """)
    conn.commit()
    # Migrate old schema if needed
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN transcript TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN clients TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN category TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN tab_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    # Ab hier: Fundament für messages/threads (Ersatz für api_histories/
    # display_histories, siehe docs-draft/JARVIS-Datenmodell-und-API.md) —
    # additiv NEBEN der bestehenden sessions-Tabelle, die unverändert weiterläuft
    # (Entfernen ist ein eigener, späterer Schritt).
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN migrated_to_messages_at TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        # Nur für die (Zwischenlösungs-)Fortsetzbarkeit von SESSION_LOAD: die id
        # der ersten zu dieser Session gehörenden messages-Zeile — siehe
        # rewind_cursor_to_session(). Bei Migration = erste migrierte Nachricht,
        # bei live neu angelegten Zeilen = Cursor-Stand zum Anlagezeitpunkt.
        conn.execute("ALTER TABLE sessions ADD COLUMN first_message_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL,
            display_text TEXT,
            attachments  TEXT,
            client_name  TEXT,
            category     TEXT NOT NULL,
            tab_id       TEXT,
            thread_id    INTEGER,
            project_id   INTEGER,
            data_scope   TEXT NOT NULL DEFAULT 'own',
            created_at   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_windows (
            category         TEXT NOT NULL,
            tab_id           TEXT NOT NULL DEFAULT '',
            active_after_id  INTEGER NOT NULL DEFAULT 0,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (category, tab_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            title             TEXT,
            project_id        INTEGER,
            last_activity_at  TEXT,
            summary           TEXT,
            data_scope        TEXT NOT NULL DEFAULT 'own'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_summaries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL UNIQUE,
            text        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            data_scope  TEXT NOT NULL DEFAULT 'own'
        )
    """)
    conn.commit()
    return conn


def _extract_text(content) -> str:
    """Extrahiert den Text-Anteil einer Message für den persistierten Transcript.
    Reine Tool-Nachrichten (nur tool_use/tool_result-Blöcke, kein Text-Block) wurden
    bisher stillschweigend zu einem leeren String — das hat sie beim Aufbau von
    transcript_msgs komplett verschluckt und unsichtbare Lücken in den persistierten
    Verlauf gerissen (2026-07-22: Restore nach einem Neustart zeigte plötzlich große
    Teile eines tool-lastigen Gesprächs nicht mehr — die Turns dazwischen waren genau
    so verschwunden). Jetzt bleibt wenigstens ein kurzer Platzhalter übrig."""
    if not isinstance(content, list):
        return str(content) if content else ""
    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    text = " ".join(t for t in text_parts if t)
    if text:
        return text
    kinds = sorted({b.get("type") for b in content if isinstance(b, dict) and b.get("type")})
    return f"[{', '.join(kinds)}]" if kinds else ""


_PLACEHOLDER_RE = re.compile(r"^\[[a-z_]+(?:, [a-z_]+)*\]$")


def is_placeholder_text(text: str) -> bool:
    """True für die von _extract_text() erzeugten Platzhalter (z.B. '[tool_result]') —
    kein echter Gesprächsinhalt. Beim Session-Restore darf daraus keine Chat-Blase
    werden (2026-07-23: Simon sah '[tool_result]' als eigene Nachricht im Chat)."""
    return bool(_PLACEHOLDER_RE.match(text))


def _first_user_message(history: list[dict]) -> str:
    for msg in history:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            if content:
                text = str(content).strip()
                return text[:60] + ("…" if len(text) > 60 else "")
    return "Gespräch"


# ── Public API ────────────────────────────────────────────────────────────────

def save(history: list[dict], clients: list[str] | None = None, category: str | None = None,
         first_message_id: int | None = None) -> threading.Thread:
    """Speichert die Session mit vollständigem Transcript. Kein LLM-Call.

    clients: Namen der Clients, die zu dieser Session beigetragen haben —
    nur für Anzeige/Filterung in jarvis-web, keine Auswirkung auf den Inhalt.
    category: "voice" oder "web" — welche History-Kategorie das war.
    first_message_id: id der ersten zu dieser (jetzt beendeten) Session gehörenden
    messages-Zeile — vom Aufrufer VOR dem Cursor-Vorrücken zu ermitteln (siehe
    server.py::_check_satellite_timeout/SESSION_RESET), ermöglicht später
    rewind_cursor_to_session() bei SESSION_LOAD. None wenn unbekannt/irrelevant.
    """
    def _do_save():
        if not history:
            return
        now = datetime.now()
        title = _first_user_message(history)

        # Nur user/assistant Messages serialisieren
        transcript_msgs = []
        for msg in history:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(msg.get("content", ""))
            if text:
                transcript_msgs.append({"role": role, "text": text})

        if not transcript_msgs:
            return

        transcript_json = json.dumps(transcript_msgs, ensure_ascii=False)
        clients_json = json.dumps(sorted(set(clients or [])), ensure_ascii=False)

        with _get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (date, time, title, transcript, clients, category, first_message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now.date().isoformat(), now.strftime("%H:%M"), title, transcript_json, clients_json, category, first_message_id)
            )
        print(f"[session] Gespeichert: {title} ({len(transcript_msgs)} Nachrichten)", flush=True)

        # Lernextraktion im Hintergrund
        try:
            import learning
            learning.process_session(history)
        except Exception as e:
            print(f"[session] Learning-Start Fehler: {e}", flush=True)

    t = threading.Thread(target=_do_save, daemon=False)
    t.start()
    return t


def upsert(session_id: int | None, history: list[dict], clients: list[str] | None = None,
           category: str | None = None, finalize: bool = False, tab_id: str | None = None,
           first_message_id: int | None = None) -> int | None:
    """Wie save(), aber schreibt bei jeder Nachricht durch statt erst beim Verbindungsende
    — sonst geht eine Konversation komplett verloren, wenn z.B. ein Browser-Tab einfach
    geschlossen wird, ohne dass ein Neustart oder "+ Neuer Chat" je einen Save auslöst
    (2026-07-20 entdeckt: Tab zu, Inhalt war für immer weg, weder im Verlauf noch sonstwo).

    session_id=None → legt eine neue Zeile an (Titel = erste User-Nachricht), sonst wird
    die bestehende Zeile aktualisiert (Titel bleibt). Gibt die (neue oder bestehende)
    session_id zurück — vom Aufrufer zu merken und beim nächsten Aufruf wieder mitzugeben.

    tab_id wird NUR beim Anlegen der Zeile (session_id=None) gesetzt und danach nie wieder
    verändert — spätere upsert()-Aufrufe für dieselbe Zeile müssen tab_id nicht mitgeben.
    Ermöglicht find_active_session(): einen wiederverbindenden Web-Tab nach einem
    Server-Neustart an seiner letzten Zeile wiederzuerkennen (2026-07-22: 'der Chat
    sollte schon wieder mitgegeben werden' — vorher hatte ein Web-Tab über einen
    Neustart hinweg keine stabile Identität, jede neue Verbindung fing bei null an,
    obwohl der Verlauf die ganze Zeit schon in sessions.db lag).

    finalize=True stößt zusätzlich die Lernextraktion an — NUR bei echtem Abschluss
    (Session-Reset, Session-Wechsel, Shutdown), nicht bei jedem Zwischen-Save, sonst
    würde jede einzelne Nachricht einen zusätzlichen LLM-Call kosten.

    first_message_id: wie bei save() — nur beim Neuanlegen (session_id=None) relevant.
    Wenn nicht angegeben, wird der aktuelle Cursor-Stand (get_cursor()) für
    category/tab_id als Fallback verwendet — an dieser Stelle (erste Nachricht einer
    NEUEN Session, direkt nach einem Reset/Load) korrekt, weil sich der Cursor
    zwischen Reset und dieser ersten Nachricht nicht mehr bewegt."""
    if not history:
        return session_id

    now = datetime.now()
    transcript_msgs = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg.get("content", ""))
        if text:
            transcript_msgs.append({"role": role, "text": text})

    if not transcript_msgs:
        return session_id

    transcript_json = json.dumps(transcript_msgs, ensure_ascii=False)
    clients_json = json.dumps(sorted(set(clients or [])), ensure_ascii=False)

    with _get_db() as conn:
        if session_id is None:
            title = _first_user_message(history)
            fmid = first_message_id
            if fmid is None and category is not None and tab_id is not None:
                fmid = get_cursor(category, tab_id) + 1
            cur = conn.execute(
                "INSERT INTO sessions (date, time, title, transcript, clients, category, tab_id, first_message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now.date().isoformat(), now.strftime("%H:%M"), title, transcript_json, clients_json, category, tab_id, fmid)
            )
            session_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE sessions SET time = ?, transcript = ?, clients = ? WHERE id = ?",
                (now.strftime("%H:%M"), transcript_json, clients_json, session_id)
            )

    if finalize:
        print(f"[session] Abgeschlossen (id={session_id}): {len(transcript_msgs)} Nachrichten", flush=True)
        try:
            import learning
            learning.process_session(history)
        except Exception as e:
            print(f"[session] Learning-Start Fehler: {e}", flush=True)

    return session_id


def find_active_session(tab_id: str) -> dict | None:
    """Für die Wiederherstellung eines Web-Tabs nach einem Server-Neustart: findet
    die zuletzt aktualisierte Session-Zeile für genau diesen tab_id. Gibt {"id",
    "transcript"} zurück (transcript = Liste von {"role", "text"}, wie von upsert()
    gespeichert) oder None wenn keine Zeile existiert."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT id, transcript FROM sessions WHERE tab_id = ? ORDER BY id DESC LIMIT 1",
                (tab_id,),
            ).fetchone()
        if row and row[1]:
            return {"id": row[0], "transcript": json.loads(row[1])}
    except Exception as e:
        print(f"[session] find_active_session Fehler: {e}", flush=True)
    return None


def list_sessions(limit: int = 30) -> list[dict]:
    """Gibt eine Liste vergangener Sessions für die UI zurück."""
    try:
        with _get_db() as conn:
            rows = conn.execute(
                """SELECT id, date, time, title, clients, category
                   FROM sessions
                   ORDER BY date DESC, time DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
    except Exception:
        return []

    result = []
    for row in rows:
        try:
            clients = json.loads(row[4]) if row[4] else []
        except Exception:
            clients = []
        result.append({
            "id": row[0], "date": row[1], "time": row[2], "title": row[3] or "Gespräch",
            "clients": clients, "category": row[5],
        })
    return result


def get_transcript(session_id: int) -> list[dict]:
    """Gibt den vollständigen Transcript einer Session zurück."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT transcript FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        print(f"[session] get_transcript Fehler: {e}", flush=True)
    return []


def delete(session_id: int) -> bool:
    """Löscht eine Session aus der Datenbank."""
    try:
        with _get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        print(f"[session] Session {session_id} gelöscht.", flush=True)
        return True
    except Exception as e:
        print(f"[session] delete Fehler: {e}", flush=True)
        return False


def load_for_prompt(days: int = 3) -> str:
    """Legacy — nicht mehr in context.py verwendet. Bleibt für Kompatibilität."""
    return ""


# ── messages/threads — Ersatz für api_histories/display_histories ─────────────
#
# Ein durchgehender Strom statt Session-Behältern (siehe docs-draft/JARVIS-
# Datenmodell-und-API.md). thread_id/project_id existieren als Spalten, werden
# hier noch nicht befüllt/ausgewertet — Fensterbildung bleibt "die letzten N
# Nachrichten dieses Tabs" wie bisher, nur aus SQLite statt aus einer
# In-Memory-Liste. category/tab_id sind eine bewusste Zwischenlösung (siehe
# ROADMAP.md) bis thread_id die Fensterbildung übernimmt.

def _cursor_tab_key(category: str, tab_id: str) -> str:
    """'voice' teilt sich EINE Historie über alle Tabs/Räume hinweg (gewollt,
    siehe server.py-Kommentar zu api_histories) — tab_id ist dort keine gültige
    Fenster-Grenze. '' ist der feste Sentinel-Key für history_windows in diesem Fall."""
    return tab_id if category != "voice" else ""


def append_message(category: str, tab_id: str, role: str, content, display_text: str | None = None,
                    attachments: list[dict] | None = None, client_name: str | None = None,
                    data_scope: str = "own", created_at: str | None = None) -> int:
    """Persistiert eine einzelne Nachricht. Aufgerufen an JEDER Stelle, an der
    pipeline.py heute self.history/client_messages einen neuen Eintrag anhängt
    (siehe pipeline.py::process_text/_run_llm) — kein Diff am Ende einer Runde.

    content: exakt der Wert, der ins Anthropic-API-`content`-Feld ginge (String
    oder eine Liste reiner Dicts als Content-Blöcke) — wird hier JSON-encoded
    gespeichert, nie verlustbehaftet geglättet. SDK-Objekte (z.B. final.content
    aus einem Streaming-Response) müssen vom Aufrufer vorher in reine Dicts
    umgewandelt werden (siehe pipeline.py::_serialize_content).

    display_text: nur setzen wenn die UI-Anzeige vom API-Inhalt abweichen soll
    (z.B. Coding-Job-Ergebnis: kurze Version an die API, volle an die UI) — sonst
    None, dann leitet die UI die Anzeige aus content ab.

    created_at: nur für die Migration alter sessions-Zeilen (dort ist nur das
    Session-Datum bekannt, nicht der echte Zeitpunkt jeder einzelnen Nachricht) —
    im Normalfall None, dann wird "jetzt" verwendet."""
    with _get_db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (role, content, display_text, attachments, client_name, "
            "category, tab_id, data_scope, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                role,
                json.dumps(content, ensure_ascii=False),
                display_text,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                client_name, category, tab_id, data_scope,
                created_at or datetime.now().isoformat(),
            ),
        )
    return cur.lastrowid


def max_message_id(category: str, tab_id: str) -> int:
    """Höchste messages.id für (category, tab_id) — 0 wenn noch keine existiert.
    Für 'voice' kategorie-weit (siehe _cursor_tab_key)."""
    with _get_db() as conn:
        if category == "voice":
            row = conn.execute("SELECT MAX(id) FROM messages WHERE category = ?", (category,)).fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(id) FROM messages WHERE category = ? AND tab_id = ?", (category, tab_id)
            ).fetchone()
    return row[0] or 0


def delete_messages_after(category: str, tab_id: str, after_id: int) -> None:
    """Löscht alle Nachrichten mit id > after_id für (category, tab_id) — einzige
    bewusste Ausnahme vom Anhängen-nie-löschen-Prinzip des Stroms, exakt begrenzt
    auf das Rollback einer komplett fehlgeschlagenen Runde (siehe pipeline.py::
    process_text, turn_start_id). Filtert exakt auf den beim Schreiben verwendeten
    tab_id (auch bei 'voice' — jede Pipeline-Instanz schreibt mit ihrem eigenen
    self._tab_id, auch wenn Voice beim LESEN tab-übergreifend fenstert)."""
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM messages WHERE category = ? AND tab_id = ? AND id > ?",
            (category, tab_id, after_id),
        )


def get_cursor(category: str, tab_id: str) -> int:
    """Aktueller Fenster-Anfang (exklusiv) für (category, tab_id) — 0 wenn noch
    nie gesetzt (Default, kein Zeilen-Eintrag nötig)."""
    key_tab = _cursor_tab_key(category, tab_id)
    with _get_db() as conn:
        row = conn.execute(
            "SELECT active_after_id FROM history_windows WHERE category = ? AND tab_id = ?",
            (category, key_tab),
        ).fetchone()
    return row[0] if row else 0


def _set_cursor(category: str, tab_id: str, value: int) -> None:
    key_tab = _cursor_tab_key(category, tab_id)
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO history_windows (category, tab_id, active_after_id, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(category, tab_id) DO UPDATE SET active_after_id = excluded.active_after_id, "
            "updated_at = excluded.updated_at",
            (category, key_tab, value, datetime.now().isoformat()),
        )


def advance_cursor(category: str, tab_id: str) -> int:
    """Ersatz für 'Liste leeren' (SESSION_RESET/8h-Timeout): Cursor auf die
    aktuell höchste message.id setzen — alles Bisherige fällt aus dem Fenster,
    ohne dass eine Zeile gelöscht wird. Gibt den ALTEN Cursor-Stand zurück (vom
    Aufrufer als first_message_id-1 an save()/upsert() für die gerade beendete
    Session weiterzureichen, siehe dortige Docstrings)."""
    old = get_cursor(category, tab_id)
    new = max_message_id(category, tab_id)
    _set_cursor(category, tab_id, new)
    return old


def session_belongs_to_tab(session_id: int, category: str, tab_id: str) -> bool:
    """Für SESSION_LOAD: darf tab_id diese (alte, in der sessions-Tabelle stehende)
    Session laden? 'voice' hat nur eine geteilte Historie, tab_id ist dort keine
    Identität — dort immer erlaubt. Für 'web' nur wenn die Session ursprünglich
    demselben tab_id gehörte (echte Cross-Tab-Fortsetzung ist ein Thread-Feature,
    hier bewusst nicht unterstützt, siehe ROADMAP.md)."""
    if category == "voice":
        return True
    with _get_db() as conn:
        row = conn.execute("SELECT tab_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return bool(row) and row[0] == tab_id


def rewind_cursor_to_session(category: str, tab_id: str, session_id: int) -> bool:
    """Für SESSION_LOAD: Cursor auf den Punkt VOR der ersten Nachricht der
    gegebenen alten Session zurücksetzen — das Fenster umfasst danach automatisch
    genau diese alte Session plus alles, was seither in diesem Tab dazukam, neue
    Runden hängen sich natürlich weiter an ("fortsetzen"). Aufrufer sollte vorher
    session_belongs_to_tab() geprüft haben. Gibt False zurück wenn die Session
    keine first_message_id hat (z.B. eine leere Alt-Session) — dann bleibt der
    Cursor unverändert."""
    with _get_db() as conn:
        row = conn.execute("SELECT first_message_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row or row[0] is None:
        return False
    _set_cursor(category, tab_id, row[0] - 1)
    return True


def build_history_window(category: str, tab_id: str, limit: int = 150) -> list[dict]:
    """Baut das Prompt-Fenster frisch aus SQLite — Ersatz für list(self.history).
    Kompression (compress_tool_history/compress_attachment_history) wendet der
    Aufrufer (pipeline.py) an, NICHT hier — session_memory.py kennt bewusst keine
    LLM-spezifische Logik. content kommt deserialisiert zurück (json.loads)."""
    cursor = get_cursor(category, tab_id)
    with _get_db() as conn:
        if category == "voice":
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE category = ? AND id > ? ORDER BY id DESC LIMIT ?",
                (category, cursor, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE category = ? AND tab_id = ? AND id > ? "
                "ORDER BY id DESC LIMIT ?",
                (category, tab_id, cursor, limit),
            ).fetchall()
    return [{"role": r[0], "content": json.loads(r[1])} for r in reversed(rows)]


def migrate_sessions_to_messages() -> None:
    """Einmaliger, idempotenter Migrationslauf beim Serverstart: überträgt alle
    noch nicht migrierten sessions-Zeilen (transcript, bereits verlustbehaftet
    durch _extract_text — Tool-Blöcke sind dort schon zu Platzhaltertext wie
    '[tool_result]' reduziert) als einzelne messages-Zeilen. Überspringt Zeilen,
    die migrated_to_messages_at bereits gesetzt haben — zweiter Lauf ist ein
    No-Op. history_windows bleibt danach leer (kein Cursor-Sonderfall nötig —
    das LIMIT beim Lesen sorgt von selbst dafür, dass praktisch die letzte,
    jüngste Session dominiert, sobald mehr als `limit` Nachrichten vorliegen).
    Die sessions-Tabelle selbst bleibt unverändert bestehen (nicht entfernt)."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, date, time, transcript, category, tab_id FROM sessions "
            "WHERE migrated_to_messages_at IS NULL"
        ).fetchall()
    if not rows:
        return

    migrated = 0
    for sid, date_, time_, transcript_json, category, tab_id in rows:
        first_id = None
        if transcript_json:
            try:
                transcript = json.loads(transcript_json)
            except Exception:
                transcript = []
            created_at = f"{date_}T{time_}" if date_ and time_ else datetime.now().isoformat()
            for msg in transcript:
                role = msg.get("role")
                text = msg.get("text", "")
                if role not in ("user", "assistant") or not text:
                    continue
                mid = append_message(
                    category=category or "voice", tab_id=tab_id or "",
                    role=role, content=text, created_at=created_at,
                )
                if first_id is None:
                    first_id = mid
        with _get_db() as conn:
            conn.execute(
                "UPDATE sessions SET migrated_to_messages_at = ?, first_message_id = ? WHERE id = ?",
                (datetime.now().isoformat(), first_id, sid),
            )
        migrated += 1
    print(f"[session] Migration: {migrated} alte Sessions nach messages übertragen.", flush=True)
