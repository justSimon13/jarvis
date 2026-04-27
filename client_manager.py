"""
ClientManager — verwaltet verbundene Clients, aktiven Client und Audio-Routing.
"""
import threading


class ClientManager:
    def __init__(self):
        self._clients: dict[str, callable] = {}  # client_id → send_audio(pcm: bytes)
        self._active: str | None = None
        self._lock = threading.Lock()

    def register(self, client_id: str, send_audio: callable):
        with self._lock:
            self._clients[client_id] = send_audio
            if self._active is None:
                self._active = client_id

    def unregister(self, client_id: str):
        with self._lock:
            self._clients.pop(client_id, None)
            if self._active == client_id:
                self._active = next(iter(self._clients), None)

    def set_active(self, client_id: str):
        with self._lock:
            if client_id in self._clients:
                self._active = client_id

    def get_active(self) -> str | None:
        with self._lock:
            return self._active

    def send_audio_to(self, client_id: str, pcm: bytes):
        with self._lock:
            cb = self._clients.get(client_id)
        if cb:
            cb(pcm)

    def send_audio_to_active(self, pcm: bytes):
        with self._lock:
            cb = self._clients.get(self._active) if self._active else None
        if cb:
            cb(pcm)

    def broadcast_event(self, event: dict):
        with self._lock:
            callbacks = list(self._clients.values())
        for cb in callbacks:
            try:
                cb(event)
            except Exception:
                pass

    @property
    def connected(self) -> list[str]:
        with self._lock:
            return list(self._clients.keys())
