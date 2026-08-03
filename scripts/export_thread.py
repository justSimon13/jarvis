"""
Exportiert einen Chat-Thread als lesbares Markdown.

Zweck: Verläufe außerhalb der Oberfläche durchsehen und besprechen — vor allem
Job-lastige Programmiergespräche, wo die Chat-Ansicht Tool-Aufrufe bewusst
versteckt und man den tatsächlichen Ablauf deshalb nicht sieht.

Bewusst nur lesend. Kein Schreibzugriff auf sessions.db, keine Änderung an
Nachrichten — das Skript kann nichts kaputt machen.

Aufruf:
    python3 scripts/export_thread.py --list
    python3 scripts/export_thread.py 42
    python3 scripts/export_thread.py 42 --out /pfad/thread-42.md
    python3 scripts/export_thread.py 42 --full     # Tool-Ergebnisse ungekürzt
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".jarvis" / "sessions.db"

# Tool-Ergebnisse sind oft mehrere Kilobyte (Dateiinhalte, Suchtreffer). Für das
# Nachvollziehen des Ablaufs zählt, WELCHES Werkzeug lief und was grob zurückkam,
# nicht der vollständige Inhalt. Mit --full abschaltbar.
_RESULT_CHARS = 400
_INPUT_CHARS = 300


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"[export] Nicht gefunden: {DB_PATH}", flush=True)
        sys.exit(1)
    # Nur-Lese-Verbindung: das Skript soll auch bei laufendem Server sicher sein.
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def list_threads(limit: int) -> None:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT t.id, t.title, t.project_id, t.last_activity_at, "
            "  (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) "
            "FROM threads t ORDER BY t.last_activity_at DESC LIMIT ?", (limit,)
        ).fetchall()
    if not rows:
        print("Keine Threads vorhanden.")
        return
    print(f"{'ID':>5}  {'Nachr.':>6}  {'Zuletzt':<19}  Titel")
    for tid, title, project_id, last, count in rows:
        stamp = (last or "")[:19].replace("T", " ")
        label = title or "(unbenannt)"
        if project_id:
            label += f"  [Projekt {project_id}]"
        print(f"{tid:>5}  {count:>6}  {stamp:<19}  {label}")


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [{len(text)} Zeichen gekürzt]"


def _render_content(content, full: bool) -> list[str]:
    """Ein Content-Feld wird zu Markdown-Zeilen.

    content ist entweder ein String (einfache Nachricht) oder eine Liste von
    Blöcken. Tool-Aufrufe und -Ergebnisse erscheinen als eigene, erkennbare
    Zeilen — genau die versteckt die Chat-Ansicht, und für die Frage "warum lief
    der Job so" sind sie das Interessanteste.
    """
    if isinstance(content, str):
        return [content.strip()] if content.strip() else []

    lines: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                lines.append(text)
        elif btype == "thinking":
            think = (block.get("thinking") or "").strip()
            if think:
                lines.append(f"> _Denkprozess:_ {_shorten(think, 600 if not full else 10**9)}")
        elif btype == "tool_use":
            args = json.dumps(block.get("input", {}), ensure_ascii=False)
            lines.append(f"**→ {block.get('name', '?')}**  `{_shorten(args, _INPUT_CHARS if not full else 10**9)}`")
        elif btype == "tool_result":
            raw = block.get("content", "")
            if isinstance(raw, list):
                raw = " ".join(
                    b.get("text", "") for b in raw
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            body = _shorten(str(raw), _RESULT_CHARS if not full else 10**9)
            lines.append(f"**← Ergebnis**\n```\n{body}\n```")
        elif btype in ("image", "document"):
            lines.append(f"_[{btype} angehängt]_")
    return lines


def export(thread_id: int, out_path: Path | None, full: bool) -> None:
    with _connect() as conn:
        thread = conn.execute(
            "SELECT id, title, project_id, last_activity_at, created_at, summary "
            "FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT id, role, content, display_text, client_name, created_at "
            "FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)
        ).fetchall()

    if not rows:
        print(f"[export] Thread {thread_id} hat keine Nachrichten "
              f"{'(existiert nicht)' if not thread else ''}", flush=True)
        sys.exit(1)

    title = (thread[1] if thread else None) or f"Thread {thread_id}"
    out: list[str] = [f"# {title}", ""]
    out.append(f"Thread {thread_id} · {len(rows)} Nachrichten")
    if thread and thread[2]:
        out.append(f"Projekt: {thread[2]}")
    if thread and thread[4]:
        out.append(f"Angelegt: {str(thread[4])[:19].replace('T', ' ')}")
    if thread and thread[5]:
        out.append(f"\nVerdichtung: {thread[5]}")
    out.append("")
    out.append("---")
    out.append("")

    for msg_id, role, content_json, display_text, client_name, created_at in rows:
        try:
            content = json.loads(content_json)
        except Exception:
            content = content_json

        stamp = (created_at or "")[:19].replace("T", " ")
        who = "Simon" if role == "user" else "JARVIS"
        if client_name:
            who += f" ({client_name})"
        out.append(f"### {who} · {stamp} · #{msg_id}")
        out.append("")

        body = _render_content(content, full)
        # display_text nur zeigen, wenn es tatsächlich abweicht — bei
        # Coding-Job-Ergebnissen steht dort etwas anderes als im API-Inhalt.
        if display_text and display_text.strip() and display_text.strip() not in "\n".join(body):
            body.append(f"_Angezeigt:_ {display_text.strip()}")
        out.extend(body or ["_(leer)_"])
        out.append("")

    text = "\n".join(out)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        print(f"[export] {len(rows)} Nachrichten → {out_path} ({len(text)} Zeichen)", flush=True)
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat-Thread als Markdown exportieren")
    parser.add_argument("thread_id", nargs="?", type=int, help="ID des Threads")
    parser.add_argument("--list", action="store_true", help="Vorhandene Threads auflisten")
    parser.add_argument("--limit", type=int, default=40, help="Wie viele Threads auflisten")
    parser.add_argument("--out", type=Path, help="Zieldatei (sonst Ausgabe im Terminal)")
    parser.add_argument("--full", action="store_true",
                        help="Tool-Ergebnisse und Denkprozesse ungekürzt")
    args = parser.parse_args()

    if args.list or args.thread_id is None:
        list_threads(args.limit)
        return 0
    export(args.thread_id, args.out, args.full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
