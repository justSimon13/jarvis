"""
JarvisPipeline — Herzstück des JARVIS-Systems.
Kapselt STT → LLM → TTS. Unabhängig von Transport (WebSocket, lokal, Text).
Eine Instanz pro Client.
"""
from __future__ import annotations
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
_STRIP_PARENS = re.compile(r'\([^)]*\)')
_NON_ALPHA = re.compile(r'[^\w]', re.UNICODE)
_MIN_MEANINGFUL = 3
TTS_BUFFER_MIN = 120


def _is_noise(text: str) -> bool:
    """True wenn text keine bedeutsame Sprache enthält (nur Geräuschbeschreibungen, Kurzfüller etc.)"""
    if not text:
        return True
    cleaned = _STRIP_PARENS.sub('', text).strip()
    return len(_NON_ALPHA.sub('', cleaned)) < _MIN_MEANINGFUL


class JarvisPipeline:
    def __init__(self, client_id: str, on_event, on_audio=None,
                 shared_history: list | None = None,
                 history_lock: threading.Lock | None = None,
                 llm_semaphore: threading.Semaphore | None = None,
                 room: str | None = None):
        """
        client_id      : eindeutiger Name des Clients
        on_event       : callable(event: dict)
        on_audio       : callable(pcm: bytes) | None
        shared_history : geteilte History aller Clients (vom Server übergeben)
        history_lock   : Lock für thread-sicheren History-Zugriff
        llm_semaphore  : stellt sicher dass nur ein LLM-Call gleichzeitig läuft (FIFO)
        room           : Raumname für Dynamic Prompt (= Client-Name)
        """
        self.client_id = client_id
        self._on_event = on_event
        self._on_audio = on_audio
        self.history: list[dict] = shared_history if shared_history is not None else []
        self._history_lock = history_lock or threading.Lock()
        self._llm_semaphore = llm_semaphore or threading.Semaphore(1)
        self._room = room or client_id

    def set_room(self, room: str):
        self._room = room

    # ── Öffentliche Methoden ──────────────────────────────────────────────────

    def process_audio(self, wav_bytes: bytes):
        """WAV-Bytes → STT → process_text()"""
        t_audio_recv = time.monotonic()
        print(f"[pipeline] Audio empfangen: {len(wav_bytes)/1024:.1f}KB", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name

        self._emit(P.STATUS, text="Transkribiere…")
        user_text = stt.transcribe(wav_path)
        t_stt_done = time.monotonic()
        print(f"[pipeline] STT fertig: {t_stt_done - t_audio_recv:.2f}s gesamt seit Empfang", flush=True)
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        if _is_noise(user_text):
            self._emit(P.STATE, state="idle")
            return

        self._emit(P.TRANSCRIPT, text=user_text)
        self.process_text(user_text, use_tts=self._on_audio is not None)

    def process_text(self, text: str, use_tts: bool = True):
        """Text → LLM → (TTS) → Events. Semaphore stellt FIFO sicher (kein paralleler LLM-Call)."""
        with self._llm_semaphore:
            with self._history_lock:
                self.history.append({"role": "user", "content": text})
                history_snapshot = list(self.history)

            context.refresh_if_stale()
            system_static = context.build_static_prompt()
            system_dynamic = context.build_dynamic_prompt(room=self._room)

            self._emit(P.STATE, state="thinking")
            response = self._run_llm(system_static, system_dynamic, history_snapshot, use_tts=use_tts)

            if response:
                with self._history_lock:
                    self.history.append({"role": "assistant", "content": response})
                    if len(self.history) > 20:
                        del self.history[:-20]

            self._emit(P.STATE, state="idle")

    def save_session(self):
        with self._history_lock:
            snapshot = list(self.history)
        if snapshot:
            t = session_memory.save(snapshot)
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
            t_llm_start = time.monotonic()
            t_first_token = None

            try:
                with llm.stream(system_static, system_dynamic, client_messages, tools.DEFINITIONS) as s:
                    for chunk in s.text_stream:
                        if not response_started:
                            self._emit(P.RESPONSE_START)
                            response_started = True
                            t_first_token = time.monotonic()
                            print(f"[pipeline] LLM erstes Token: {t_first_token - t_llm_start:.2f}s", flush=True)

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
                                            print(f"[pipeline] TTS erster Satz gesendet: {time.monotonic() - t_llm_start:.2f}s seit LLM-Start", flush=True)
                                        tts_queue.put(send)
                                else:
                                    break

                    final = s.get_final_message()
                    print(f"[pipeline] LLM fertig: {time.monotonic() - t_llm_start:.2f}s, stop={final.stop_reason}", flush=True)

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
