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

    # Kurze Begrüßung damit der Client weiß dass er verbunden ist
    print("[server] Starte Greeting…", flush=True)
    try:
        await loop.run_in_executor(None, pipeline.process_text, "Sag nur: Bereit.", True)
        print("[server] Greeting fertig.", flush=True)
    except Exception as e:
        print(f"[server] Greeting Fehler: {e}", flush=True)

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                manager.set_active(client_id)
                await loop.run_in_executor(None, pipeline.process_audio, message)
            else:
                data = json.loads(message)
                if data.get("type") == P.TEXT_INPUT:
                    manager.set_active(client_id)
                    use_tts = data.get("tts", True)
                    await loop.run_in_executor(
                        None, pipeline.process_text, data["text"], use_tts
                    )
                elif data.get("type") == P.CLIENT_HELLO:
                    name = data.get("name", "")
                    if name:
                        manager.set_name(client_id, name)
                        print(f"[server] Client {addr} heißt jetzt: {name!r}")
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
