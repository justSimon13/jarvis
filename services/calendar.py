import json
import time
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from services import google_auth
import config

_CACHE_PATH = config.JARVIS_DIR / "calendar_cache.json"
_CACHE_TTL = 15 * 60

_service = None


def _get_service():
    global _service
    if _service is None:
        creds = google_auth.get_credentials()
        _service = build("calendar", "v3", credentials=creds)
    return _service


def _format_time(dt_str: str) -> str:
    try:
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%H:%M")
        return dt_str  # Ganztages-Event
    except Exception:
        return dt_str


def query(days_ahead: int = 1) -> list[dict]:
    if not google_auth.is_configured():
        return []
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    try:
        result = _get_service().events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = []
        for e in result.get("items", []):
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            end_t = e["end"].get("dateTime", e["end"].get("date", ""))
            events.append({
                "title": e.get("summary", "(kein Titel)"),
                "start": start,
                "end": end_t,
                "start_fmt": _format_time(start),
                "location": e.get("location"),
                "event_id": e["id"],
            })
        return events
    except Exception as e:
        print(f"[calendar] Fehler: {e}")
        return []


def query_cached(days_ahead: int = 1) -> list[dict]:
    if _CACHE_PATH.exists():
        try:
            cached = json.loads(_CACHE_PATH.read_text())
            if time.time() - cached.get("fetched_at", 0) < _CACHE_TTL:
                return cached.get("events", [])
        except Exception:
            pass
    events = query(days_ahead)
    try:
        _CACHE_PATH.write_text(json.dumps({"fetched_at": int(time.time()), "events": events}))
    except Exception:
        pass
    return events


def write(title: str, start_iso: str, end_iso: str, description: str = "") -> str:
    if not google_auth.is_configured():
        return "Google Calendar nicht konfiguriert."
    event = {
        "summary": title,
        "start": {"dateTime": start_iso, "timeZone": "Europe/Berlin"},
        "end": {"dateTime": end_iso, "timeZone": "Europe/Berlin"},
    }
    if description:
        event["description"] = description
    try:
        created = _get_service().events().insert(calendarId="primary", body=event).execute()
        _CACHE_PATH.unlink(missing_ok=True)
        return f"Event '{title}' erstellt."
    except Exception as e:
        return f"Fehler beim Erstellen: {e}"


def delete(event_id: str) -> str:
    if not google_auth.is_configured():
        return "Google Calendar nicht konfiguriert."
    try:
        _get_service().events().delete(calendarId="primary", eventId=event_id).execute()
        _CACHE_PATH.unlink(missing_ok=True)
        return "Event gelöscht."
    except Exception as e:
        return f"Fehler beim Löschen: {e}"


def format_for_prompt() -> str:
    events = query_cached()
    if not events:
        return ""
    lines = ["## Heutiger Kalender"]
    for e in events:
        line = f"- {e['start_fmt']}: {e['title']}"
        if e.get("location"):
            line += f" ({e['location']})"
        lines.append(line)
    return "\n".join(lines)
