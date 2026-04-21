import subprocess
import sys


def _not_supported() -> str:
    return "Apple Music ist nur auf macOS verfügbar."


def _run(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def get_current_track() -> dict:
    if sys.platform != "darwin":
        return {"error": _not_supported()}
    script = """
    tell application "Music"
        if player state is playing then
            return name of current track & " | " & artist of current track & " | " & album of current track
        else
            return "nicht am laufen"
        end if
    end tell
    """
    out = _run(script)
    if out == "nicht am laufen":
        return {"playing": False}
    parts = out.split(" | ")
    return {
        "playing": True,
        "title": parts[0] if len(parts) > 0 else "",
        "artist": parts[1] if len(parts) > 1 else "",
        "album": parts[2] if len(parts) > 2 else "",
    }


def play_pause() -> str:
    if sys.platform != "darwin":
        return _not_supported()
    _run('tell application "Music" to playpause')
    state = _run('tell application "Music" to get player state as string')
    return "Spielt." if "playing" in state else "Pausiert."


def next_track() -> str:
    if sys.platform != "darwin":
        return _not_supported()
    _run('tell application "Music" to next track')
    return get_current_track().get("title", "Nächster Track.")


def previous_track() -> str:
    if sys.platform != "darwin":
        return _not_supported()
    _run('tell application "Music" to previous track')
    return get_current_track().get("title", "Vorheriger Track.")


def set_volume(level: int) -> str:
    if sys.platform != "darwin":
        return _not_supported()
    level = max(0, min(100, level))
    _run(f'tell application "Music" to set sound volume to {level}')
    return f"Lautstärke auf {level}%."


def play_search(query: str) -> str:
    if sys.platform != "darwin":
        return _not_supported()
    script = f"""
    tell application "Music"
        set results to search library playlist 1 for "{query}"
        if results is {{}} then
            return "Nichts gefunden."
        else
            play first item of results
            return name of current track & " von " & artist of current track
        end if
    end tell
    """
    out = _run(script)
    return out if out else "Nichts gefunden."
