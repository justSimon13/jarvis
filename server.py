"""
J.A.R.V.I.S. WebSocket-Server — läuft auf dem HP EliteDesk (Linux, 24/7).
Eine geteilte History für alle Clients — JARVIS kennt Gespräche raumübergreifend.
Gesichert via Tailscale (kein eigenes Auth nötig).

Start: python3 server.py
"""
import asyncio
import json
import os
import threading
from datetime import date

import websockets

import brain
import config
import context
import protocol as P
import stt
from client_manager import ClientManager
from pipeline import JarvisPipeline
from services import alarm as alarm_service
from services import client_music as client_music_service
from services import sleep_coach
from services import proactive as proactive_service

HOST = os.getenv("JARVIS_HOST", "0.0.0.0")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

manager = ClientManager()

# Geteilte History + Synchronisations-Primitive für alle Clients
shared_history: list[dict] = []
history_lock = threading.Lock()
llm_semaphore = threading.Semaphore(1)


def _build_layout_config() -> dict:
    """Berechnet server-seitig welche Cards angezeigt werden sollen."""
    alarms = []
    try:
        alarms = alarm_service.list_alarms()
    except Exception:
        pass

    followups_due = []
    try:
        followups_raw = brain.read("followups") or {}
        if isinstance(followups_raw, dict):
            today_iso = date.today().isoformat()
            followups_due = [
                k for k, v in followups_raw.items()
                if v and (not isinstance(v, dict) or not v.get("due") or v.get("due") <= today_iso)
            ]
    except Exception:
        pass

    cards = [
        {"id": "todos",    "type": "list",   "title": "Todos heute", "source": "todos_today"},
        {"id": "calendar", "type": "agenda", "title": "Heute",       "source": "calendar_today"},
        {"id": "btc",      "type": "metric", "title": "BTC",         "source": "btc"},
    ]
    if alarms:
        cards.append({"id": "alarms", "type": "list", "title": "Wecker", "source": "alarms"})
    if followups_due:
        cards.append({"id": "followups", "type": "list", "title": "Offene Punkte", "source": "followups_due"})

    return {
        "cards": cards,
        "quick_actions": [
            {
                "id": "alarm",
                "label": "Wecker",
                "icon": "⏰",
                "input": {"type": "time_picker", "label": "Weckzeit"},
                "send": "Stell einen Wecker für {value} Uhr.",
            },
            {
                "id": "todo_add",
                "label": "Todo +",
                "icon": "📋",
                "input": {"type": "text", "placeholder": "Was muss erledigt werden?"},
                "send": "Erstelle ein Todo: {value}",
            },
            {
                "id": "checkin",
                "label": "Check-In",
                "icon": "💬",
                "input": None,
                "send": "Mach einen kurzen Morgen Check-In.",
            },
        ],
    }


def _handle_data_request(resource: str):
    if resource == "todos":
        conn = context._get_db()
        data = context._get_cached(conn, "todos") or []
        conn.close()
        return data
    if resource == "calendar":
        try:
            from services import calendar as cal_service
            return cal_service.query_cached(days_ahead=7)
        except Exception:
            return []
    if resource == "alarms":
        try:
            return alarm_service.list_alarms()
        except Exception:
            return []
    if resource == "followups":
        try:
            return brain.read("followups") or {}
        except Exception:
            return {}
    if resource == "clients":
        return manager.list_clients()
    if resource == "btc":
        try:
            from services import btc as btc_service
            return btc_service.get_price()
        except Exception:
            return {}
    return None


def _build_dashboard_sync() -> dict:
    conn = context._get_db()
    todos = context._get_cached(conn, "todos") or []
    conn.close()
    btc_data: dict = {}
    try:
        from services import btc as btc_service
        btc_data = btc_service.get_price()
    except Exception:
        pass
    cal_events: list = []
    try:
        from services import calendar as cal_service
        cal_events = cal_service.query_cached(days_ahead=7)
    except Exception:
        pass
    alarms: list = []
    try:
        alarms = alarm_service.list_alarms()
    except Exception:
        pass
    followups: dict = {}
    try:
        followups = brain.read("followups") or {}
    except Exception:
        pass
    return {
        "type": P.DASHBOARD_SYNC,
        "todos": todos,
        "calendar": cal_events,
        "btc": btc_data,
        "clients": manager.list_clients(),
        "alarms": alarms,
        "followups": followups,
        "layout_config": _build_layout_config(),
    }


def _activate_client(client_id: str):
    """Setzt aktiven Client und triggert Musik-Raumwechsel falls nötig."""
    prev_active = manager.get_active()
    manager.set_active(client_id)
    if prev_active != client_id:
        new_name = manager.get_name(client_id)
        if new_name:
            client_music_service.on_room_change(new_name)


async def _push_dashboard_update():
    loop = asyncio.get_event_loop()
    try:
        payload = await loop.run_in_executor(None, _build_dashboard_sync)
        payload["type"] = P.DASHBOARD_UPDATE
        payload["layout_config"] = _build_layout_config()
        for cb in manager.get_dashboard_event_callbacks():
            try:
                cb(payload)
            except Exception:
                pass
    except Exception as e:
        print(f"[server] Dashboard-Update Fehler: {e}", flush=True)


async def handle_connection(websocket):
    loop = asyncio.get_event_loop()
    client_id = str(id(websocket))
    addr = websocket.remote_address
    print(f"[server] Client verbunden: {addr} ({client_id})")

    def send_json(event: dict):
        asyncio.run_coroutine_threadsafe(
            websocket.send(json.dumps(event, ensure_ascii=False)), loop
        )

    def send_audio(pcm: bytes):
        asyncio.run_coroutine_threadsafe(websocket.send(pcm), loop)

    pipeline = JarvisPipeline(
        client_id=client_id,
        on_event=send_json,
        on_audio=send_audio,
        shared_history=shared_history,
        history_lock=history_lock,
        llm_semaphore=llm_semaphore,
    )

    manager.register(client_id, send_audio)
    manager.register_event(client_id, send_json)
    manager.register_pipeline(client_id, pipeline)
    send_json({"type": P.STATE, "state": "idle"})

    # Warte kurz auf CLIENT_HELLO um Role und Raumname zu erkennen
    role = "client"
    pending_msgs = []
    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=1.5)
        pending_msgs.append(first_raw)
        if isinstance(first_raw, str):
            first_data = json.loads(first_raw)
            if first_data.get("type") == P.CLIENT_HELLO:
                name = first_data.get("name", "")
                role = first_data.get("role", "client")
                if name:
                    manager.set_name(client_id, name)
                    manager.set_role(client_id, role)
                    pipeline.set_room(name)
                    print(f"[server] Client {addr} heißt: {name!r} (role={role})")
                pending_msgs.clear()
    except asyncio.TimeoutError:
        pass

    if role == "dashboard":
        send_json(_build_dashboard_sync())
        print("[server] Dashboard-Sync gesendet.", flush=True)
    else:
        # Begrüßung für Voice-Clients
        print("[server] Starte Greeting…", flush=True)
        try:
            await loop.run_in_executor(None, pipeline.process_text, "Sag nur: Bereit.", True)
            print("[server] Greeting fertig.", flush=True)
        except Exception as e:
            print(f"[server] Greeting Fehler: {e}", flush=True)

    try:
        # Ausstehende Nachrichten (falls CLIENT_HELLO noch nicht verarbeitet) zuerst
        for raw in pending_msgs:
            if isinstance(raw, bytes):
                manager.set_active(client_id)
                await loop.run_in_executor(None, pipeline.process_audio, raw)
            else:
                try:
                    data = json.loads(raw)
                    if data.get("type") == P.CLIENT_HELLO:
                        name = data.get("name", "")
                        r = data.get("role", "client")
                        if name:
                            manager.set_name(client_id, name)
                            manager.set_role(client_id, r)
                except Exception:
                    pass

        async for message in websocket:
            if isinstance(message, bytes):
                _activate_client(client_id)
                await loop.run_in_executor(None, pipeline.process_audio, message)
                asyncio.create_task(_push_dashboard_update())
            else:
                data = json.loads(message)
                if data.get("type") == P.TEXT_INPUT:
                    _activate_client(client_id)
                    use_tts = data.get("tts", True)
                    await loop.run_in_executor(
                        None, pipeline.process_text, data["text"], use_tts
                    )
                    asyncio.create_task(_push_dashboard_update())
                elif data.get("type") == P.CLIENT_HELLO:
                    name = data.get("name", "")
                    role = data.get("role", "client")
                    if name:
                        manager.set_name(client_id, name)
                        manager.set_role(client_id, role)
                        print(f"[server] Client {addr} heißt jetzt: {name!r} (role={role})")
                    if role == "dashboard":
                        send_json(_build_dashboard_sync())
                elif data.get("type") == P.DATA_REQUEST:
                    resource = data.get("resource", "")
                    result = await loop.run_in_executor(None, _handle_data_request, resource)
                    send_json({"type": P.DATA_RESPONSE, "resource": resource, "data": result})
                elif data.get("type") == P.ALARM_SYNC:
                    client_name = manager.get_name(client_id) or ""
                    alarm_service.sync_from_client(client_name, data.get("alarms", []))
                    print(f"[server] Alarm-Sync von {client_name!r}: {len(data.get('alarms', []))} Wecker")
                elif data.get("type") == P.ALARM_RINGING:
                    alarm_service.on_ringing(data["alarm_id"])
                    print(f"[server] Wecker klingelt: {data.get('label')!r} auf {manager.get_name(client_id)!r}")
                elif data.get("type") == P.ALARM_DISMISSED:
                    client_name = manager.get_name(client_id) or ""
                    alarm_service.on_dismissed(data["alarm_id"], data.get("snooze_count", 0))
                elif data.get("type") == P.PING:
                    await websocket.send(json.dumps({"type": P.PONG}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        manager.unregister(client_id)
        if role != "dashboard":
            pipeline.save_session()
        print(f"[server] Client getrennt: {addr}")


async def main():
    brain.sync()
    context.refresh_if_stale()
    stt.load_model()
    alarm_service.init(manager)
    client_music_service.init(manager)
    sleep_coach.init(manager, alarm_service)
    proactive_service.init(manager, alarm_service)
    print(f"[server] J.A.R.V.I.S. bereit — ws://{HOST}:{PORT}")
    async with websockets.serve(
        handle_connection, HOST, PORT,
        ping_interval=30,
        ping_timeout=120,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
