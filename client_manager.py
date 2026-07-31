"""
ClientManager — verwaltet verbundene Clients, aktiven Client und Audio-Routing.
"""
from __future__ import annotations
import threading


class ClientManager:
    def __init__(self):
        self._clients: dict[str, callable] = {}        # client_id → send_audio(pcm: bytes)
        self._event_handlers: dict[str, callable] = {} # client_id → send_event(dict)
        self._names: dict[str, str] = {}               # client_id → name
        self._roles: dict[str, str] = {}               # client_id → role ("client"|"dashboard")
        self._modes: dict[str, str] = {}               # client_id → mode ("assistent"|"coach"|"fokus")
        self._pipelines: dict[str, object] = {}        # client_id → JarvisPipeline
        self._capabilities: dict[str, list[str]] = {}  # client_id → ["local_exec", ...] (z.B. Tauri-Desktop-App)
        self._worker_ids: dict[str, str] = {}           # client_id → worker_id (nur Tauri-Desktop-App, sonst nicht gesetzt)
        # tab_id → client_id (nur "web"-Kategorie relevant) — client_id ist ein
        # server-internes, PRO VERBINDUNG neues Handle (str(id(websocket)) in
        # server.py), tab_id dagegen die vom Browser stabil in sessionStorage
        # gehaltene UUID. _event_handlers bleibt bewusst NUR unter client_id
        # geführt (unverändert, wie schon immer) — diese Zuordnung ist eine
        # SEPARATE Indirektion für Code, das gezielt an einen bestimmten Tab
        # zustellen will (Coding-Job-Ergebnisse/-Fortschritt), ohne
        # get_dashboard_event_callbacks()/broadcast_event() (beide iterieren
        # weiterhin nur über client_id-Keys) zu berühren — eine zweite
        # Registrierung DIREKT in _event_handlers unter tab_id wurde bewusst
        # verworfen, das hätte eine implizite, leicht brechende Abhängigkeit
        # geschaffen (ein künftiger Refactor der Broadcast-Funktionen auf eine
        # direkte _event_handlers-Iteration hätte dann still doppelt zugestellt).
        self._tab_to_client: dict[str, str] = {}
        # worker_id → Rolle ("mac-private"/"mac-work", passend zu projekte.client_id).
        # Rein In-Memory — Persistenz über einen Server-Neustart hinweg ist Sache
        # von services/coding_jobs.py (_load_worker_assignments/assign_worker,
        # brain.config), damit dieser Manager ohne I/O-Abhängigkeiten bleibt.
        self._worker_assignments: dict[str, str] = {}
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

    def get_name(self, client_id: str) -> str | None:
        with self._lock:
            return self._names.get(client_id)

    def set_role(self, client_id: str, role: str):
        with self._lock:
            self._roles[client_id] = role

    def get_role(self, client_id: str) -> str:
        with self._lock:
            return self._roles.get(client_id, "client")

    def list_clients(self) -> list[dict]:
        with self._lock:
            active = self._active
            return [
                {
                    "name": self._names.get(cid, cid),
                    "role": self._roles.get(cid, "client"),
                    "active": cid == active,
                }
                for cid in self._clients
            ]

    def register_pipeline(self, client_id: str, pipeline):
        with self._lock:
            self._pipelines[client_id] = pipeline

    def get_active_pipeline(self):
        with self._lock:
            return self._pipelines.get(self._active) if self._active else None

    def set_mode(self, client_id: str, mode: str):
        with self._lock:
            self._modes[client_id] = mode

    def get_mode(self, client_id: str) -> str:
        with self._lock:
            return self._modes.get(client_id, "assistent")

    def set_capabilities(self, client_id: str, capabilities: list[str]):
        with self._lock:
            self._capabilities[client_id] = list(capabilities or [])

    def set_worker_id(self, client_id: str, worker_id: str | None):
        """Kennung des Mac-Workers aus client_hello (nur Tauri-Desktop-App, sonst None).
        Noch nicht für Routing genutzt (get_client_with_capability nimmt weiterhin den
        ersten Treffer) — legt nur den Wire-Format-Schnitt für eine spätere
        mac-private/mac-work-Unterscheidung."""
        with self._lock:
            if worker_id:
                self._worker_ids[client_id] = worker_id

    def get_worker_id(self, client_id: str) -> str | None:
        with self._lock:
            return self._worker_ids.get(client_id)

    def get_client_with_capability(self, capability: str) -> str | None:
        """Erster verbundener Client mit einer bestimmten Capability (z.B. 'local_exec' —
        die Tauri-Desktop-App, im Gegensatz zu einem normalen Browser-Tab). Bei mehreren
        gleichzeitig verbundenen Clients mit derselben Capability zählt der zuerst gefundene —
        für ein einzelnes Mac reicht das. Für Coding-Jobs (mehrere Mac-Worker möglich)
        NICHT mehr verwendet, siehe get_connection_for_role() — bleibt für Aufrufer, denen
        es egal ist WELCHER local_exec-Client antwortet (z.B. Ticket-Sync)."""
        with self._lock:
            for cid, caps in self._capabilities.items():
                if capability in caps:
                    return cid
            return None

    def set_worker_assignment(self, worker_id: str, role_client_id: str) -> None:
        with self._lock:
            self._worker_assignments[worker_id] = role_client_id

    def get_worker_assignment(self, worker_id: str) -> str | None:
        with self._lock:
            return self._worker_assignments.get(worker_id)

    def remove_worker_assignment(self, worker_id: str) -> None:
        """Gegenstück zu set_worker_assignment — für unassign_mac_worker (siehe
        services/coding_jobs.py::unassign_worker). Kein Fehler wenn worker_id
        gar nicht zugeordnet war (idempotent)."""
        with self._lock:
            self._worker_assignments.pop(worker_id, None)

    def get_connection_for_role(self, role_client_id: str) -> str | None:
        """Connection-ID des Mac-Workers, der aktuell der Rolle role_client_id
        (z.B. 'mac-work', passend zu projekte.client_id) zugeordnet UND
        verbunden ist. None wenn nicht zugeordnet ODER nicht verbunden — KEIN
        Fallback auf einen anderen verbundenen Worker (sonst könnte ein Job für
        ein Kundenprojekt auf dem falschen Mac landen)."""
        with self._lock:
            for cid, caps in self._capabilities.items():
                if "local_exec" not in caps:
                    continue
                worker_id = self._worker_ids.get(cid)
                if worker_id and self._worker_assignments.get(worker_id) == role_client_id:
                    return cid
            return None

    def list_local_exec_workers(self) -> list[dict]:
        """Aktuell verbundene local_exec-Clients mit worker_id + aktueller
        Rollenzuordnung (client_id: None = noch nicht zugeordnet, bekommt
        keine Coding-Jobs). Für das list_mac_workers-Tool — Simon muss einen
        neuen, unbekannten Worker im Chat sehen können, um ihn zuzuordnen."""
        with self._lock:
            result = []
            for cid, caps in self._capabilities.items():
                if "local_exec" not in caps:
                    continue
                worker_id = self._worker_ids.get(cid)
                result.append({
                    "worker_id": worker_id,
                    "client_id": self._worker_assignments.get(worker_id) if worker_id else None,
                })
            return result

    def unregister(self, client_id: str):
        with self._lock:
            self._clients.pop(client_id, None)
            self._event_handlers.pop(client_id, None)
            self._names.pop(client_id, None)
            self._roles.pop(client_id, None)
            self._modes.pop(client_id, None)
            self._pipelines.pop(client_id, None)
            self._capabilities.pop(client_id, None)
            self._worker_ids.pop(client_id, None)
            # WERTBASIERT löschen (nicht per tab_id-Schlüssel) — race-sicher bei
            # einem schnellen Reconnect: hat set_tab_client() für denselben
            # tab_id inzwischen schon die NEUE client_id eingetragen, bevor der
            # (verzögerte) unregister() der alten Verbindung hier ankommt,
            # schlägt der Wertvergleich fehl und der aktuelle Eintrag bleibt
            # unangetastet. Ein reines "del self._tab_to_client[tab_id]" hätte
            # genau diese Race gehabt.
            for tab_id, cid in list(self._tab_to_client.items()):
                if cid == client_id:
                    del self._tab_to_client[tab_id]
            if self._active == client_id:
                self._active = next(iter(self._clients), None)

    def set_tab_client(self, tab_id: str, client_id: str):
        """Merkt sich, welche client_id (aktuelle Verbindung) zu einem stabilen
        Browser-tab_id gehört — Grundlage für get_event_callback_for_tab()."""
        with self._lock:
            self._tab_to_client[tab_id] = client_id

    def get_event_callback_for_tab(self, tab_id: str) -> callable | None:
        """Wie get_event_callback(), aber über tab_id statt client_id aufgelöst
        (löst intern zu client_id auf, _event_handlers bleibt unverändert nur
        unter client_id geführt — siehe Kommentar bei self._tab_to_client)."""
        with self._lock:
            client_id = self._tab_to_client.get(tab_id)
            if not client_id:
                return None
            return self._event_handlers.get(client_id)

    def get_pipeline_for_tab(self, tab_id: str):
        """Wie get_event_callback_for_tab(), aber liefert die JarvisPipeline-Instanz
        statt des Event-Callbacks — gebraucht z.B. beim Zusammenführen zweier
        Threads (Thread-Umbau Teil A), um pipeline.set_thread() für einen
        gerade verbundenen, betroffenen Tab serverseitig nachzuziehen."""
        with self._lock:
            client_id = self._tab_to_client.get(tab_id)
            if not client_id:
                return None
            return self._pipelines.get(client_id)

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

    def get_event_callback(self, client_id: str) -> callable | None:
        """Gibt den Event-Callback für einen spezifischen Client zurück."""
        with self._lock:
            return self._event_handlers.get(client_id)

    def get_dashboard_event_callbacks(self) -> list[tuple]:
        """Gibt (callback, mode) Tupel für alle verbundenen Dashboard-Clients zurück."""
        with self._lock:
            return [
                (self._event_handlers[cid], self._modes.get(cid, "assistent"))
                for cid in self._clients
                if self._roles.get(cid) == "dashboard" and cid in self._event_handlers
            ]

    @property
    def connected(self) -> list[str]:
        with self._lock:
            return list(self._clients.keys())
