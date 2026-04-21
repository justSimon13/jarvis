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


def search_tracks(query: str, limit: int = 8) -> list[dict]:
    """Gibt mehrere Treffer mit Details zurück, ohne direkt abzuspielen."""
    if sys.platform != "darwin":
        return []
    script = f"""
    tell application "Music"
        set results to search library playlist 1 for "{query}"
        if results is {{}} then
            return ""
        end if
        set output to ""
        set n to count of results
        if n > {limit} then set n to {limit}
        repeat with i from 1 to n
            set t to item i of results
            set output to output & (i as string) & "|" & name of t & "|" & artist of t & "|" & album of t & "\\n"
        end repeat
        return output
    end tell
    """
    out = _run(script)
    tracks = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 4:
            tracks.append({"index": int(parts[0]), "title": parts[1], "artist": parts[2], "album": parts[3]})
    return tracks


def play_search(query: str) -> str:
    """Sucht Tracks und gibt Treffer zurück damit JARVIS die richtige Version wählen kann."""
    if sys.platform != "darwin":
        return _not_supported()
    tracks = search_tracks(query)
    if not tracks:
        return "Nichts gefunden."
    if len(tracks) == 1:
        return play_track_index(query, tracks[0]["index"])
    # Mehrere Treffer: als JSON zurückgeben damit Claude die richtige Version wählt
    import json
    return json.dumps({"results": tracks, "hint": "Bitte play_track aufrufen mit query und index des gewünschten Tracks."}, ensure_ascii=False)


def play_track_index(query: str, index: int) -> str:
    """Spielt einen bestimmten Treffer aus einer vorherigen Suche ab."""
    if sys.platform != "darwin":
        return _not_supported()
    script = f"""
    tell application "Music"
        set results to search library playlist 1 for "{query}"
        if (count of results) >= {index} then
            play item {index} of results
            return name of current track & " von " & artist of current track & " (" & album of current track & ")"
        else
            return "Index nicht gefunden."
        end if
    end tell
    """
    out = _run(script)
    return out if out else "Fehler beim Abspielen."
