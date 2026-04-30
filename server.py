"""
J.A.R.V.I.S. WebSocket-Server — läuft auf dem HP EliteDesk (Linux, 24/7).
Pro Client eine eigene JarvisPipeline mit separater History.
Gesichert via Tailscale (kein eigenes Auth nötig).

Start: python3 server.py
"""
import asyncio
import json
import os

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
    }


async def _push_dashboard_update():
    loop = asyncio.get_event_loop()
    try:
        payload = await loop.run_in_executor(None, _build_dashboard_sync)
        payload["type"] = P.DASHBOARD_UPDATE
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
    )

    manager.register(client_id, send_audio)
    manager.register_event(client_id, send_json)
    manager.register_pipeline(client_id, pipeline)
    send_json({"type": P.STATE, "state": "idle"})

    # Warte kurz auf CLIENT_HELLO um Role zu erkennen bevor wir begrüßen
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
                    print(f"[server] Client {addr} heißt: {name!r} (role={role})")
                pending_msgs.clear()  # already handled
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
                manager.set_active(client_id)
                await loop.run_in_executor(None, pipeline.process_audio, message)
                asyncio.create_task(_push_dashboard_update())
            else:
                data = json.loads(message)
                if data.get("type") == P.TEXT_INPUT:
                    manager.set_active(client_id)
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
