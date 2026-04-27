"""
JarvisEngine — WebSocket-Client für jarvis-app (Mac GUI).
Verbindet mit dem JARVIS-Server, leitet Audio weiter und übersetzt
Server-Events in GUI-Events.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import queue
import threading

import numpy as np

import audio
import config
import protocol as P
from enum import Enum


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TOOL_RUNNING = "tool_running"


_STATE_MAP = {s.value: s for s in State}


class JarvisEngine(threading.Thread):
    def __init__(self, events_out: queue.Queue, commands_in: queue.Queue):
        super().__init__(daemon=True, name="JarvisEngine")
        self.events_out = events_out
        self.commands_in = commands_in
        self.mode = "voice"
        self._stop = threading.Event()
        self._wake_interrupt = threading.Event()
        self._ws = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    def run(self):
        asyncio.run(self._run_client())

    # ── WebSocket-Client ──────────────────────────────────────────────────────

    async def _run_client(self):
        import websockets

        server = config.JARVIS_SERVER
        if not server:
            self._emit("error", "JARVIS_SERVER nicht gesetzt. Bitte .env konfigurieren.")
            return

        self._emit("status_text", f"Verbinde mit {server}…")

        while not self._stop.is_set():
            try:
                async with websockets.connect(server, ping_interval=20) as ws:
                    self._ws = ws
                    self._ws_loop = asyncio.get_event_loop()
                    self._emit("status_text", "Verbunden")

                    audio_queue: queue.Queue = queue.Queue()
                    threading.Thread(
                        target=self._play_loop, args=(audio_queue,), daemon=True
                    ).start()
                    threading.Thread(
                        target=self._record_loop, daemon=True
                    ).start()

                    await asyncio.gather(
                        self._recv_loop(ws, audio_queue),
                        self._cmd_loop(ws),
                    )
            except Exception as e:
                self._ws = None
                self._ws_loop = None
                if self._stop.is_set():
                    break
                self._emit("status_text", f"Reconnect in 5s… ({type(e).__name__})")
                await asyncio.sleep(5)

    async def _recv_loop(self, ws, audio_queue: queue.Queue):
        async for message in ws:
            if isinstance(message, bytes):
                audio_queue.put(message)
            else:
                self._handle_server_event(json.loads(message))
        audio_queue.put(None)

    async def _cmd_loop(self, ws):
        while not self._stop.is_set():
            await asyncio.sleep(0.1)
            try:
                while True:
                    cmd, data = self.commands_in.get_nowait()
                    if cmd == "stop":
                        self._stop.set()
                        return
                    elif cmd == "text_input":
                        await ws.send(json.dumps({
                            "type": P.TEXT_INPUT,
                            "text": data,
                            "tts": False,
                        }))
                    elif cmd == "set_mode":
                        self.mode = data
                        self._wake_interrupt.set()
                        self._emit("mode_changed", data)
            except queue.Empty:
                pass

    def _handle_server_event(self, data: dict):
        t = data.get("type")
        if t == P.STATE:
            self._emit("state", _STATE_MAP.get(data.get("state", "idle"), State.IDLE))
        elif t == P.STATUS:
            self._emit("status_text", data.get("text", ""))
        elif t == P.TRANSCRIPT:
            self._emit("user_text", data.get("text", ""))
        elif t == P.RESPONSE_START:
            self._emit("response_start", None)
        elif t == P.RESPONSE_CHUNK:
            self._emit("response_chunk", data.get("text", ""))
        elif t == P.RESPONSE_DONE:
            self._emit("response_done", data.get("text", ""))
        elif t == P.TOOL:
            self._emit("tool_running", data.get("name", ""))
        elif t == P.ERROR:
            self._emit("error", data.get("message", ""))

    def _record_loop(self):
        in_conversation = False
        silent_turns = 0

        while not self._stop.is_set():
            if self.mode == "text":
                import time
                time.sleep(0.3)
                continue

            if not in_conversation:
                if not config.MANUAL_MODE:
                    self._emit("status_text", "Warte auf Wake Word…")
                    self._wake_interrupt.clear()
                    try:
                        audio.listen_for_wake_word(interrupt=self._wake_interrupt)
                    except Exception:
                        if self._stop.is_set():
                            break
                        continue
                    if self._stop.is_set():
                        break
                    if self.mode != "voice":
                        continue

            self._emit("state", State.LISTENING)
            self._emit("status_text", "Ich höre…")

            try:
                wav_path = audio.record_with_vad(interrupt=self._wake_interrupt)
            except Exception:
                wav_path = None

            if not wav_path:
                silent_turns += 1
                if silent_turns >= 2:
                    in_conversation = False
                    silent_turns = 0
                continue

            silent_turns = 0
            in_conversation = True

            try:
                with open(wav_path, "rb") as f:
                    wav_bytes = f.read()
                import os
                os.unlink(wav_path)
            except OSError:
                continue

            if self._ws and self._ws_loop:
                asyncio.run_coroutine_threadsafe(
                    self._ws.send(wav_bytes), self._ws_loop
                )

    def _play_loop(self, audio_queue: queue.Queue):
        import sounddevice as sd
        with sd.OutputStream(
            samplerate=P.PCM_SAMPLERATE,
            channels=P.PCM_CHANNELS,
            dtype=P.PCM_DTYPE,
        ) as stream:
            while True:
                chunk = audio_queue.get()
                if chunk is None:
                    break
                stream.write(np.frombuffer(chunk, dtype=np.int16))

    # ── Gemeinsam ─────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, data=None):
        self.events_out.put((event_type, data))

    def stop(self):
        self._stop.set()
        self.commands_in.put(("stop", None))

    def set_mode(self, mode: str):
        self.mode = mode
        self._wake_interrupt.set()
        self.commands_in.put(("set_mode", mode))
        self._emit("mode_changed", mode)
