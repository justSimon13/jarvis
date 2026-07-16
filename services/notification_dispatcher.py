"""
NotificationDispatcher — echter Push-Layer, unabhängig von der Pipeline.

notify() kann von überall aufgerufen werden: ProactiveDaemon, SleepCoach,
TodoReminder — ohne dass gerade eine Konversation läuft.

Ablauf:
  1. notify() → in SQLite speichern (überlebt Neustart)
  2. notify() → sofort an alle verbundenen Dashboard-Clients senden
  3. deliver_pending() → beim Client-Connect: was noch aussteht nachliefern
  4. mark_delivered() → Client schickt notification_ack → delivered_at setzen
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_DB_PATH = Path.home() / ".jarvis" / "notifications.db"
_MAX_PER_HOUR = 3


class NotificationDispatcher:

    def __init__(self, client_manager):
        # Referenz auf ClientManager — damit wir wissen wer gerade verbunden ist
        self._manager = client_manager
        self._lock = threading.Lock()
        self._db = self._open_db()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _open_db(self) -> sqlite3.Connection:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id           TEXT PRIMARY KEY,
                text         TEXT NOT NULL,
                channels     TEXT NOT NULL,
                priority     TEXT NOT NULL DEFAULT 'normal',
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                delivered_at TEXT
            )
        """)
        conn.commit()
        return conn

    # ── Public API ────────────────────────────────────────────────────────────

    def notify(
        self,
        text: str,
        channels: list[str] | None = None,
        priority: str = "normal",
        expires_in_min: int = 60,
    ) -> str | None:
        """
        Erstellt eine Notification und liefert sie sofort aus wenn Clients verbunden.

        channels: ["dashboard"] | ["voice"] | ["dashboard", "voice"]
        priority: "low" | "normal" | "high"
        expires_in_min: danach wird die Notification nicht mehr nachgeliefert

        Gibt die notification_id zurück, oder None wenn Rate-Limit greift.
        """
        if channels is None:
            channels = ["dashboard"]

        if not self._under_rate_limit():
            print("[dispatcher] Rate-Limit erreicht — Notification verworfen", flush=True)
            return None

        now = datetime.utcnow()
        notification = {
            "id":           str(uuid.uuid4()),
            "text":         text,
            "channels":     json.dumps(channels),
            "priority":     priority,
            "created_at":   now.isoformat(),
            "expires_at":   (now + timedelta(minutes=expires_in_min)).isoformat(),
            "delivered_at": None,
        }

        with self._lock:
            self._db.execute(
                "INSERT INTO notifications VALUES "
                "(:id, :text, :channels, :priority, :created_at, :expires_at, :delivered_at)",
                notification,
            )
            self._db.commit()

        print(f"[dispatcher] Notification: {text[:80]}", flush=True)
        self._deliver_now(notification)
        return notification["id"]

    def deliver_pending(self, client_id: str):
        """
        Beim Client-Connect aufrufen.
        Liefert alle ausstehenden, nicht-abgelaufenen Notifications an diesen Client.
        """
        role = self._manager.get_role(client_id)
        if role != "dashboard":
            return

        cb = self._manager.get_event_callback(client_id)
        if not cb:
            return

        now = datetime.utcnow().isoformat()
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, channels, priority, expires_at FROM notifications "
                "WHERE delivered_at IS NULL AND expires_at > ?",
                (now,),
            ).fetchall()

        for nid, text, channels_json, priority, expires_at in rows:
            channels = json.loads(channels_json)
            if "dashboard" not in channels:
                continue
            try:
                cb({
                    "type":     "notification_push",
                    "id":       nid,
                    "text":     text,
                    "priority": priority,
                    "expires":  expires_at,
                })
                self.mark_delivered(nid)
                print(f"[dispatcher] Pending nachgeliefert: {text[:60]}", flush=True)
            except Exception as e:
                print(f"[dispatcher] Pending-Delivery Fehler: {e}", flush=True)

    def mark_delivered(self, notification_id: str):
        """Setzt delivered_at — aufgerufen wenn Client notification_ack schickt."""
        with self._lock:
            self._db.execute(
                "UPDATE notifications SET delivered_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), notification_id),
            )
            self._db.commit()

    def cleanup_expired(self):
        """Löscht zugestellte, abgelaufene Notifications. 1× täglich aufrufen."""
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._db.execute(
                "DELETE FROM notifications "
                "WHERE expires_at < ? AND delivered_at IS NOT NULL",
                (now,),
            )
            self._db.commit()
        print("[dispatcher] Expired Notifications bereinigt", flush=True)

    # ── Intern ────────────────────────────────────────────────────────────────

    def _deliver_now(self, notification: dict):
        """Sofortzustellung an alle aktuell verbundenen Dashboard-Clients."""
        channels = json.loads(notification["channels"])
        delivered = False

        if "dashboard" in channels:
            for cb, _ in self._manager.get_dashboard_event_callbacks():
                try:
                    cb({
                        "type":     "notification_push",
                        "id":       notification["id"],
                        "text":     notification["text"],
                        "priority": notification["priority"],
                        "expires":  notification["expires_at"],
                    })
                    delivered = True
                except Exception as e:
                    print(f"[dispatcher] Delivery-Fehler: {e}", flush=True)

        if delivered:
            self.mark_delivered(notification["id"])

    def _under_rate_limit(self) -> bool:
        one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with self._lock:
            count = self._db.execute(
                "SELECT COUNT(*) FROM notifications WHERE created_at > ?",
                (one_hour_ago,),
            ).fetchone()[0]
        return count < _MAX_PER_HOUR
