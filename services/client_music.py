"""
Client Music Service — routet PLAY_MUSIC / STOP_MUSIC an den Ziel-Satellite.
Trackt aktuelle Wiedergabe für Raum-Wechsel (Stufe 1: harter Schnitt mit Seek).
"""
import time
import protocol as P

_manager = None
_current: dict | None = None  # {song, target, started_at, volume}


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


def play(song: str, target: str | None = None, volume: int = 70) -> None:
    global _current
    _current = {
        "song": song,
        "target": target,
        "started_at": time.time(),
        "volume": volume,
    }
    _route(target, {"type": P.PLAY_MUSIC, "song": song, "volume": volume})


def stop(target: str | None = None) -> None:
    global _current
    _current = None
    _route(target, {"type": P.STOP_MUSIC})


def on_room_change(new_client_name: str) -> None:
    """Wenn Simon den Raum wechselt: Musik stoppen und auf neuer Box an gleicher Stelle weiterspielen."""
    global _current
    if not _current or not _manager:
        return
    old_target = _current.get("target")
    if old_target == new_client_name:
        return

    seek_seconds = int(time.time() - _current["started_at"])
    song = _current["song"]
    volume = _current.get("volume", 70)

    # Alte Box stoppen
    if old_target:
        _manager.send_event_to_name(old_target, {"type": P.STOP_MUSIC})
    else:
        _manager.send_event_to_active({"type": P.STOP_MUSIC})

    # Neue Box an gleicher Position starten
    _current = {
        "song": song,
        "target": new_client_name,
        "started_at": time.time() - seek_seconds,
        "volume": volume,
    }
    _manager.send_event_to_name(new_client_name, {
        "type": P.PLAY_MUSIC,
        "song": song,
        "volume": volume,
        "seek": seek_seconds,
    })
    print(f"[music] Raum-Wechsel: {old_target!r} → {new_client_name!r} bei {seek_seconds}s", flush=True)


def _route(target: str | None, event: dict) -> None:
    if not _manager:
        return
    if target:
        _manager.send_event_to_name(target, event)
    else:
        _manager.send_event_to_active(event)
