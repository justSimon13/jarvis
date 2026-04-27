import sys
import subprocess


def _run(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip()


def add_item(item: str, list_name: str = "Einkaufsliste") -> str:
    if sys.platform != "darwin":
        return "Apple Reminders ist nur auf macOS verfügbar."
    script = f'''
    tell application "Reminders"
        if not (exists list "{list_name}") then
            make new list with properties {{name:"{list_name}"}}
        end if
        tell list "{list_name}"
            make new reminder with properties {{name:"{item}"}}
        end tell
    end tell
    '''
    try:
        _run(script)
        return f"{item} zur {list_name} hinzugefügt."
    except Exception as e:
        return f"Fehler: {e}"


def get_items(list_name: str = "Einkaufsliste") -> list[str]:
    if sys.platform != "darwin":
        return []
    script = f'''
    tell application "Reminders"
        if not (exists list "{list_name}") then return ""
        set output to ""
        repeat with r in (reminders of list "{list_name}" whose completed is false)
            set output to output & name of r & linefeed
        end repeat
        return output
    end tell
    '''
    try:
        raw = _run(script)
        return [line.strip() for line in raw.splitlines() if line.strip()]
    except Exception:
        return []


def set_alarm(label: str, hour: int, minute: int) -> str:
    """Erstellt einen Wecker in Apple Reminders — synct via iCloud aufs iPhone."""
    if sys.platform != "darwin":
        return "Apple Reminders ist nur auf macOS verfügbar."
    import datetime
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    # AppleScript date format: "21.04.2026 07:30:00"
    date_str = target.strftime("%d.%m.%Y %H:%M:%S")
    script = f'''
    tell application "Reminders"
        if not (exists list "Wecker") then
            make new list with properties {{name:"Wecker"}}
        end if
        tell list "Wecker"
            make new reminder with properties {{name:"{label}", due date:date "{date_str}", remind me date:date "{date_str}"}}
        end tell
    end tell
    '''
    try:
        _run(script)
        return f"Wecker gesetzt: '{label}' um {target.strftime('%H:%M')} Uhr (synct aufs iPhone)."
    except Exception as e:
        return f"Fehler: {e}"


def remove_item(item: str, list_name: str = "Einkaufsliste") -> str:
    if sys.platform != "darwin":
        return "Apple Reminders ist nur auf macOS verfügbar."
    script = f'''
    tell application "Reminders"
        if not (exists list "{list_name}") then return "Liste nicht gefunden."
        set found to false
        repeat with r in (reminders of list "{list_name}" whose completed is false)
            if name of r is "{item}" then
                set completed of r to true
                set found to true
                exit repeat
            end if
        end repeat
        if found then
            return "ok"
        else
            return "not found"
        end if
    end tell
    '''
    try:
        result = _run(script)
        if result == "ok":
            return f"{item} von der {list_name} entfernt."
        return f"{item} nicht auf der Liste gefunden."
    except Exception as e:
        return f"Fehler: {e}"
