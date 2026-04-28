"""
ClientManager — verwaltet verbundene Clients, aktiven Client und Audio-Routing.
"""
import threading


class ClientManager:
    def __init__(self):
        self._clients: dict[str, callable] = {}        # client_id → send_audio(pcm: bytes)
        self._event_handlers: dict[str, callable] = {} # client_id → send_event(dict)
        self._names: dict[str, str] = {}               # client_id → name
        self._active: str | None = None
        self._lock = threading.Lock()

    def register(self, client_id: str, send_audio: callable):
        with self._lock:
            self._clients[client_id] = send_audio
            if self._active is None:
                self._active = client_id

    def register_event(self, client_id: str, send_event: callable):
        with self._lock:
            self._event_handlers[client_id] = send_event

    def set_name(self, client_id: str, name: str):
        with self._lock:
            self._names[client_id] = name.lower()

    def unregister(self, client_id: str):
        with self._lock:
            self._clients.pop(client_id, None)
            self._event_handlers.pop(client_id, None)
            self._names.pop(client_id, None)
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

    def send_audio_to_name(self, name: str, pcm: bytes):
        name = name.lower()
        with self._lock:
            client_id = next((cid for cid, n in self._names.items() if n == name), None)
            cb = self._clients.get(client_id) if client_id else None
        if cb:
            cb(pcm)

    def send_audio_to_active(self, pcm: bytes):
        with self._lock:
            cb = self._clients.get(self._active) if self._active else None
        if cb:
            cb(pcm)

    def send_event_to_name(self, name: str, event: dict):
        name = name.lower()
        with self._lock:
            client_id = next((cid for cid, n in self._names.items() if n == name), None)
            cb = self._event_handlers.get(client_id) if client_id else None
        if cb:
            cb(event)

    def send_event_to_active(self, event: dict):
        with self._lock:
            cb = self._event_handlers.get(self._active) if self._active else None
        if cb:
            cb(event)

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
