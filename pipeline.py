"""
JarvisPipeline — Herzstück des JARVIS-Systems.
Kapselt STT → LLM → TTS. Unabhängig von Transport (WebSocket, lokal, Text).
Eine Instanz pro Client.
"""
import os
import queue
import re
import tempfile
import threading
import time

import anthropic

import context
import llm
import protocol as P
import session_memory
import stt
import tools
import tts

SENTENCE_END = re.compile(r'([^.!?\n]{15,}[.!?\n]+)')
TTS_BUFFER_MIN = 120


class JarvisPipeline:
    def __init__(self, client_id: str, on_event, on_audio=None):
        """
        client_id : eindeutiger Name des Clients ("local", "ws-abc123", …)
        on_event  : callable(event: dict) — empfängt alle JSON-Events
        on_audio  : callable(pcm: bytes) | None — empfängt PCM-Chunks (None = kein TTS)
        """
        self.client_id = client_id
        self._on_event = on_event
        self._on_audio = on_audio
        self.history: list[dict] = []

    # ── Öffentliche Methoden ──────────────────────────────────────────────────

    def process_audio(self, wav_bytes: bytes):
        """WAV-Bytes → STT → process_text()"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name

        self._emit(P.STATUS, text="Transkribiere…")
        user_text = stt.transcribe(wav_path)
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        if not user_text:
            self._emit(P.STATE, state="idle")
            return

        self._emit(P.TRANSCRIPT, text=user_text)
        self.process_text(user_text, use_tts=self._on_audio is not None)

    def process_text(self, text: str, use_tts: bool = True):
        """Text → LLM → (TTS) → Events"""
        self.history.append({"role": "user", "content": text})
        # Nur beim ersten Turn einer Konversation refreshen — nicht nach jedem Turn.
        # Sonst sieht JARVIS seine eigenen Notion-Updates als neue Info und wiederholt sie.
        if len(self.history) == 1:
            context.refresh_if_stale()
        system_static = context.build_static_prompt()
        system_dynamic = context.build_dynamic_prompt()

        self._emit(P.STATE, state="thinking")
        response = self._run_llm(system_static, system_dynamic, self.history, use_tts=use_tts)

        if response:
            self.history.append({"role": "assistant", "content": response})
            self.history = self.history[-20:]

        self._emit(P.STATE, state="idle")

    def save_session(self):
        if self.history:
            t = session_memory.save(list(self.history))
            if t:
                t.join(timeout=10)

    # ── LLM-Loop ─────────────────────────────────────────────────────────────

    def _run_llm(
        self,
        system_static: str,
        system_dynamic: str,
        messages: list[dict],
        use_tts: bool = True,
    ) -> str:
        client_messages = messages.copy()
        full_response = ""

        while True:
            tts_queue: queue.Queue = queue.Queue()
            turn_text = ""
            buffer = ""
            tts_done = threading.Event()

            if use_tts and self._on_audio:
                threading.Thread(
                    target=tts.speak_response,
                    args=(tts_queue, tts_done, self._on_audio),
                    daemon=True,
                ).start()
            else:
                tts_done.set()

            response_started = False
            first_chunk_sent = False

            try:
                with llm.stream(system_static, system_dynamic, client_messages, tools.DEFINITIONS) as s:
                    for chunk in s.text_stream:
                        if not response_started:
                            self._emit(P.RESPONSE_START)
                            response_started = True

                        buffer += chunk
                        turn_text += chunk
                        self._emit(P.RESPONSE_CHUNK, text=chunk)

                        if use_tts and self._on_audio:
                            while True:
                                match = SENTENCE_END.search(buffer)
                                if match and match.end() >= TTS_BUFFER_MIN:
                                    send = buffer[:match.end()].strip()
                                    buffer = buffer[match.end():].lstrip()
                                    if send:
                                        if not first_chunk_sent:
                                            first_chunk_sent = True
                                            self._emit(P.STATE, state="speaking")
                                        tts_queue.put(send)
                                else:
                                    break

                    final = s.get_final_message()

            except anthropic.APIStatusError as e:
                if use_tts and self._on_audio:
                    tts_queue.put(None)
                if "overloaded" in str(e).lower():
                    self._emit(P.ERROR, message="Anthropic überlastet.")
                    time.sleep(5)
                else:
                    self._emit(P.ERROR, message=f"API-Fehler: {e}")
                return ""
            except Exception as e:
                if use_tts and self._on_audio:
                    tts_queue.put(None)
                self._emit(P.ERROR, message=f"Fehler: {e}")
                return ""

            if use_tts and self._on_audio:
                if buffer.strip():
                    tts_queue.put(buffer.strip())
                tts_queue.put(None)

            tts_done.wait()
            self._emit(P.RESPONSE_DONE, text=turn_text)

            if final.stop_reason == "end_turn":
                full_response = turn_text
                break

            if final.stop_reason == "tool_use":
                client_messages = client_messages + [{"role": "assistant", "content": final.content}]
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        self._emit(P.STATE, state="tool_running")
                        self._emit(P.TOOL, name=block.name)
                        result = tools.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                client_messages = client_messages + [{"role": "user", "content": tool_results}]
                client_messages = llm.compress_tool_history(client_messages)
                self._emit(P.STATE, state="thinking")

        return full_response

    # ── Intern ────────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, **kwargs):
        self._on_event({"type": event_type, **kwargs})
