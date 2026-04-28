"""
Client Music Service — routet PLAY_MUSIC / STOP_MUSIC an den Ziel-Satellite.
mpv + yt-dlp spielen direkt auf dem Client-Device ab.
"""
import protocol as P

_manager = None


def init(client_manager) -> None:
    global _manager
    _manager = client_manager


def play(song: str, target: str | None = None, volume: int = 70) -> None:
    _route(target, {"type": P.PLAY_MUSIC, "song": song, "volume": volume})


def stop(target: str | None = None) -> None:
    _route(target, {"type": P.STOP_MUSIC})


def _route(target: str | None, event: dict) -> None:
    if not _manager:
        return
    if target:
        _manager.send_event_to_name(target, event)
    else:
        _manager.send_event_to_active(event)
