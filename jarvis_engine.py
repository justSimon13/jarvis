import os
import re
import json
import queue
import threading
import time
from enum import Enum
import anthropic
import audio
import stt
import llm
import tts
import tools
import context
import brain
import config
import timer_service

SENTENCE_END = re.compile(r'([^.!?\n]{15,}[.!?\n]+)')
TTS_BUFFER_MIN = 120
VOICE_MAX_SECONDS = 10.0


def _seems_complete(text: str) -> bool:
    """Whisper fügt Satzzeichen bei vollständigen Sätzen hinzu — das nutzen wir."""
    text = text.strip()
    if not text:
        return False
    if text[-1] in ".!?…":
        return True
    # Lange Äußerung ohne Punkt → trotzdem verarbeiten
    return len(text.split()) >= 12


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TOOL_RUNNING = "tool_running"


def _compress_tool_result(tool_name: str, result: str) -> str:
    try:
        data = json.loads(result)
        if isinstance(data, list):
            return f"[{tool_name}: {len(data)} Einträge]"
        if isinstance(data, dict) and len(result) > 300:
            return f"[{tool_name}: OK]"
    except (json.JSONDecodeError, TypeError):
        pass
    return result if len(result) <= 300 else result[:300] + "…"


class JarvisEngine(threading.Thread):
    def __init__(self, events_out: queue.Queue, commands_in: queue.Queue):
        super().__init__(daemon=True, name="JarvisEngine")
        self.events_out = events_out
        self.commands_in = commands_in
        self.mode = "voice"
        self._stop = threading.Event()
        self._wake_interrupt = threading.Event()

    def run(self):
        try:
            self._emit("state", State.IDLE)
            timer_service.set_speak_callback(tts.speak)
            brain.sync()
            context.refresh_if_stale()
            stt.load_model()
            tts.speak("Alle Systeme bereit, Sir.")
        except Exception as e:
            self._emit("error", f"Startup-Fehler: {e}")
            return

        history: list[dict] = []
        in_conversation = False
        silent_turns = 0
        MAX_SILENT_TURNS = 2

        while not self._stop.is_set():
            self._emit("state", State.IDLE)

            if self.mode == "voice":
                if not in_conversation:
                    self._emit("status_text", "Warte auf Wake Word…")
                    self._wake_interrupt.clear()
                    try:
                        audio.listen_for_wake_word(interrupt=self._wake_interrupt)
                    except Exception:
                        if self._stop.is_set():
                            break
                        continue
                    if self.mode != "voice":
                        continue

                self._emit("state", State.LISTENING)
                self._emit("status_text", "Ich höre…")

                accumulated = ""
                start_time = time.time()
                got_speech = False

                while not self._stop.is_set() and self.mode == "voice":
                    try:
                        wav_path = audio.record_with_vad(interrupt=self._wake_interrupt)
                    except Exception as e:
                        self._emit("error", f"Aufnahme-Fehler: {e}")
                        wav_path = ""

                    if not wav_path:
                        break

                    got_speech = True
                    self._emit("status_text", "Transkribiere…")
                    chunk = stt.transcribe(wav_path)
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

                    if chunk:
                        accumulated = (accumulated + " " + chunk).strip() if accumulated else chunk
                        if _seems_complete(accumulated):
                            break
                        elapsed = time.time() - start_time
                        if elapsed >= VOICE_MAX_SECONDS:
                            break
                        self._emit("status_text", "Nutzer ist am Denken…")
                        self._emit("user_thinking", accumulated)
                    else:
                        break

                if not got_speech:
                    silent_turns += 1
                    if silent_turns >= MAX_SILENT_TURNS:
                        in_conversation = False
                        silent_turns = 0
                    continue

                user_text = accumulated

            else:  # text mode
                self._emit("status_text", "Warte auf Eingabe…")
                try:
                    cmd, data = self.commands_in.get(timeout=0.5)
                except queue.Empty:
                    continue
                if cmd == "stop":
                    break
                if cmd == "set_mode":
                    self.mode = data
                    in_conversation = False
                    continue
                user_text = data if cmd == "text_input" else ""

            if not user_text:
                silent_turns += 1
                if silent_turns >= MAX_SILENT_TURNS:
                    in_conversation = False
                    silent_turns = 0
                continue

            silent_turns = 0
            in_conversation = True
            self._emit("user_text", user_text)

            history.append({"role": "user", "content": user_text})
            context.refresh_if_stale()
            system_prompt = context.build_system_prompt()

            self._emit("state", State.THINKING)
            response = self._run_llm(system_prompt, history)

            if response:
                history.append({"role": "assistant", "content": response})
                history = history[-20:]

    def _run_llm(self, system_prompt: str, messages: list[dict]) -> str:
        client_messages = messages.copy()
        full_response = ""
        text_only = (self.mode == "text")

        while True:
            tts_queue = queue.Queue()
            turn_text = ""
            buffer = ""

            tts_done = threading.Event()
            if not text_only:
                tts_thread = threading.Thread(
                    target=tts.speak_response, args=(tts_queue, tts_done), daemon=True
                )
                tts_thread.start()

                thinking_stop = threading.Event()
                threading.Thread(
                    target=audio.play_thinking_sound, args=(thinking_stop,), daemon=True
                ).start()
            else:
                tts_done.set()
                thinking_stop = threading.Event()
                thinking_stop.set()

            first_chunk_sent = False
            self._emit("response_start", None)

            try:
                with llm.stream(system_prompt, client_messages, tools.DEFINITIONS) as s:
                    for chunk in s.text_stream:
                        buffer += chunk
                        turn_text += chunk
                        self._emit("response_chunk", chunk)

                        if not text_only:
                            while True:
                                match = SENTENCE_END.search(buffer)
                                if match and match.end() >= TTS_BUFFER_MIN:
                                    send = buffer[:match.end()].strip()
                                    buffer = buffer[match.end():].lstrip()
                                    if send:
                                        if not first_chunk_sent:
                                            thinking_stop.set()
                                            first_chunk_sent = True
                                        self._emit("state", State.SPEAKING)
                                        tts_queue.put(send)
                                else:
                                    break
                    final = s.get_final_message()

            except anthropic.APIStatusError as e:
                thinking_stop.set()
                tts_queue.put(None)
                if "overloaded" in str(e).lower():
                    self._emit("error", "Anthropic überlastet, bitte erneut versuchen.")
                    time.sleep(5)
                    if not text_only:
                        tts.speak("Entschuldigung Sir, die Server sind kurz überlastet.")
                else:
                    self._emit("error", f"API-Fehler: {e}")
                return ""
            except Exception as e:
                thinking_stop.set()
                tts_queue.put(None)
                if not self._stop.is_set():
                    self._emit("error", f"Fehler: {e}")
                return ""

            thinking_stop.set()
            if not text_only:
                if buffer.strip():
                    tts_queue.put(buffer.strip())
                tts_queue.put(None)
            tts_done.wait()

            self._emit("response_done", turn_text)

            if final.stop_reason == "end_turn":
                full_response = turn_text
                break

            if final.stop_reason == "tool_use":
                client_messages = client_messages + [{"role": "assistant", "content": final.content}]
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        self._emit("state", State.TOOL_RUNNING)
                        self._emit("tool_running", block.name)
                        result = tools.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                client_messages = client_messages + [{"role": "user", "content": tool_results}]
                self._emit("state", State.THINKING)

        return full_response

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
