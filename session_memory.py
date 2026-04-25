import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
import anthropic
import config

_SESSIONS_FILE = Path.home() / ".jarvis" / "brain" / "sessions.jsonl"


def _summarize(history: list[dict]) -> dict | None:
    if not history:
        return None

    turns = []
    for msg in history[-20:]:
        role = "Simon" if msg["role"] == "user" else "JARVIS"
        content = msg["content"]
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            turns.append(f"{role}: {str(content)[:200]}")

    if not turns:
        return None

    conversation_text = "\n".join(turns)

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system="Du analysierst Gespräche zwischen Simon und seinem KI-Assistenten JARVIS. Antworte nur mit validem JSON, kein Markdown.",
            messages=[{
                "role": "user",
                "content": (
                    "Analysiere dieses Gespräch und gib JSON zurück:\n"
                    '{"summary": "2-3 Sätze: Was wurde besprochen, entschieden, getan?", '
                    '"context": ["Max 3 kurze Hinweise zu Simons Stimmung/Zustand/Situation"], '
                    '"follow_ups": ["Max 3 offene Punkte die beim nächsten Gespräch relevant sein könnten"]}\n\n'
                    f"Gespräch:\n{conversation_text}"
                ),
            }],
        )
        text = response.content[0].text.strip()
        return json.loads(text)
    except Exception as e:
        print(f"[session] Zusammenfassung fehlgeschlagen: {e}")
        return None


def save(history: list[dict]) -> threading.Thread:
    """Session asynchron zusammenfassen und speichern. Gibt Thread zurück zum joinen."""
    def _do_save():
        summary_data = _summarize(history)
        if not summary_data:
            return
        now = datetime.now()
        entry = {
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M"),
            **summary_data,
        }
        _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SESSIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[session] Gespeichert: {entry['summary'][:60]}...")

    t = threading.Thread(target=_do_save, daemon=False)
    t.start()
    return t


def load_for_prompt(days: int = 3) -> str:
    """Letzte Sessions als System-Prompt-Abschnitt formatieren."""
    if not _SESSIONS_FILE.exists():
        return ""

    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    recent = []

    try:
        with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("date", "") >= cutoff:
                        recent.append(entry)
                except json.JSONDecodeError:
                    pass
    except Exception:
        return ""

    if not recent:
        return ""

    lines = ["## Letzte Sessions"]
    for entry in recent[-5:]:
        date_str = entry.get("date", "")
        time_str = entry.get("time", "")
        summary = entry.get("summary", "")
        context_items = entry.get("context", [])
        follow_ups = entry.get("follow_ups", [])

        line = f"- {date_str} {time_str}: {summary}"
        if context_items:
            line += f" | Kontext: {', '.join(context_items)}"
        if follow_ups:
            line += f" | Offen: {', '.join(follow_ups)}"
        lines.append(line)

    return "\n".join(lines)
