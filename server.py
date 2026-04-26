"""
J.A.R.V.I.S. WebSocket-Server — läuft auf dem HP EliteDesk (Linux, 24/7).
Jeder verbundene Client bekommt eine eigene JarvisSession mit eigener History.
Gesichert via Tailscale (kein eigenes Auth nötig).

Start: python3 server.py
"""

import asyncio
import json
import os
import queue
import re
import tempfile
import threading

import websockets

import brain
import context
import llm
import stt
import tts
import tools
import session_memory
import config
import protocol as P

HOST = os.getenv("JARVIS_HOST", "0.0.0.0")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

SENTENCE_END = re.compile(r'([^.!?\n]{15,}[.!?\n]+)')
TTS_BUFFER_MIN = 120


class JarvisSession:
    def __init__(self, websocket, loop: asyncio.AbstractEventLoop):
        self.ws = websocket
        self.loop = loop
        self.history: list[dict] = []

    # ── Thread-safe Senden ────────────────────────────────────────────────────

    def _send(self, data: bytes | str):
        asyncio.run_coroutine_threadsafe(self.ws.send(data), self.loop)

    def _emit(self, obj: dict):
        self._send(json.dumps(obj, ensure_ascii=False))

    # ── Audio-Verarbeitung ────────────────────────────────────────────────────

    def handle_audio(self, wav_bytes: bytes):
        """Executor-Thread: WAV → STT → LLM → TTS."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name

        self._emit({"type": P.STATUS, "text": "Transkribiere…"})
        user_text = stt.transcribe(wav_path)
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        if not user_text:
            self._emit({"type": P.STATE, "state": "idle"})
            return

        self._emit({"type": P.TRANSCRIPT, "text": user_text})
        self.handle_text(user_text)

    def handle_text(self, user_text: str):
        """Executor-Thread: Text → LLM → TTS."""
        self.history.append({"role": "user", "content": user_text})
        context.refresh_if_stale()
        system_static = context.build_static_prompt()
        system_dynamic = context.build_dynamic_prompt()

        self._emit({"type": P.STATE, "state": "thinking"})
        response = self._run_llm(system_static, system_dynamic, self.history)

        if response:
            self.history.append({"role": "assistant", "content": response})
            self.history = self.history[-20:]

        self._emit({"type": P.STATE, "state": "idle"})

    def _run_llm(self, system_static: str, system_dynamic: str, messages: list[dict]) -> str:
        client_messages = messages.copy()
        full_response = ""

        while True:
            tts_queue: queue.Queue = queue.Queue()
            turn_text = ""
            buffer = ""
            tts_done = threading.Event()

            tts_thread = threading.Thread(
                target=tts.stream_response_audio,
                args=(tts_queue, tts_done, self._send),
                daemon=True,
            )
            tts_thread.start()

            response_started = False

            try:
                with llm.stream(system_static, system_dynamic, client_messages, tools.DEFINITIONS) as s:
                    for chunk in s.text_stream:
                        if not response_started:
                            self._emit({"type": P.RESPONSE_START})
                            response_started = True
                        buffer += chunk
                        turn_text += chunk
                        self._emit({"type": P.RESPONSE_CHUNK, "text": chunk})

                        while True:
                            match = SENTENCE_END.search(buffer)
                            if match and match.end() >= TTS_BUFFER_MIN:
                                send = buffer[:match.end()].strip()
                                buffer = buffer[match.end():].lstrip()
                                if send:
                                    self._emit({"type": P.STATE, "state": "speaking"})
                                    tts_queue.put(send)
                            else:
                                break
                    final = s.get_final_message()

            except Exception as e:
                tts_queue.put(None)
                self._emit({"type": P.ERROR, "message": str(e)})
                return ""

            if buffer.strip():
                tts_queue.put(buffer.strip())
            tts_queue.put(None)
            tts_done.wait()
            self._emit({"type": P.RESPONSE_DONE, "text": turn_text})

            if final.stop_reason == "end_turn":
                full_response = turn_text
                break

            if final.stop_reason == "tool_use":
                client_messages = client_messages + [{"role": "assistant", "content": final.content}]
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        self._emit({"type": P.STATE, "state": "tool_running"})
                        self._emit({"type": P.TOOL, "name": block.name})
                        result = tools.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                client_messages = client_messages + [{"role": "user", "content": tool_results}]
                client_messages = llm.compress_tool_history(client_messages)
                self._emit({"type": P.STATE, "state": "thinking"})

        return full_response


# ── WebSocket Handler ─────────────────────────────────────────────────────────

async def handle_connection(websocket):
    loop = asyncio.get_event_loop()
    session = JarvisSession(websocket, loop)
    addr = websocket.remote_address
    print(f"[server] Client verbunden: {addr}")
    await websocket.send(json.dumps({"type": P.STATE, "state": "idle"}))

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                await loop.run_in_executor(None, session.handle_audio, message)
            else:
                data = json.loads(message)
                if data.get("type") == P.TEXT_INPUT:
                    await loop.run_in_executor(None, session.handle_text, data["text"])
                elif data.get("type") == P.PING:
                    await websocket.send(json.dumps({"type": P.PONG}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(f"[server] Client getrennt: {addr}")
        if session.history:
            session_memory.save(list(session.history))


async def main():
    brain.sync()
    context.refresh_if_stale()
    stt.load_model()
    print(f"[server] J.A.R.V.I.S. Server bereit — ws://{HOST}:{PORT}")
    async with websockets.serve(handle_connection, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
