import os
import sys
import re
import queue
import threading
from enum import Enum
import audio
import stt
import llm
import tts
import tools
import context
import brain
import config


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TOOL_RUNNING = "tool_running"


_state = State.IDLE

SENTENCE_END = re.compile(r'([^.!?\n]{20,}[.!?\n])')


def set_state(s: State):
    global _state
    _state = s


def _start_tts_worker(tts_queue: queue.Queue) -> threading.Thread:
    def worker():
        while True:
            text = tts_queue.get()
            if text is None:
                break
            set_state(State.SPEAKING)
            tts.speak_stream(text)
            tts_queue.task_done()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def _run_with_streaming_tts(system_prompt: str, messages: list[dict]) -> str:
    """Führt LLM aus mit Satz-Streaming zu TTS. Behandelt Tool Use korrekt."""
    client_messages = messages.copy()
    full_response = ""

    while True:
        tts_queue = queue.Queue()
        worker = _start_tts_worker(tts_queue)
        buffer = ""
        turn_text = ""

        print("J.A.R.V.I.S.: ", end="", flush=True)

        with llm.stream(system_prompt, client_messages, tools.DEFINITIONS) as s:
            for chunk in s.text_stream:
                print(chunk, end="", flush=True)
                buffer += chunk
                turn_text += chunk
                set_state(State.THINKING)

                while True:
                    match = SENTENCE_END.match(buffer)
                    if match:
                        sentence = match.group(1).strip()
                        buffer = buffer[match.end():].lstrip()
                        if sentence:
                            tts_queue.put(sentence)
                    else:
                        break

            final = s.get_final_message()

        # Restpuffer sprechen
        if buffer.strip():
            tts_queue.put(buffer.strip())
        tts_queue.put(None)
        worker.join()
        print()

        if final.stop_reason == "end_turn":
            full_response = turn_text
            break

        if final.stop_reason == "tool_use":
            client_messages = client_messages + [{"role": "assistant", "content": final.content}]
            tool_results = []
            for block in final.content:
                if block.type == "tool_use":
                    set_state(State.TOOL_RUNNING)
                    print(f"[tool] {block.name}({block.input})")
                    result = tools.execute(block.name, block.input)
                    print(f"[tool] → {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            client_messages = client_messages + [{"role": "user", "content": tool_results}]
            set_state(State.THINKING)

    return full_response


def main():
    print("J.A.R.V.I.S. startet...")

    brain.sync()
    context.refresh_if_stale()
    system_prompt = context.build_system_prompt()
    stt.load_model()

    wake_word_mode = bool(config.PICOVOICE_ACCESS_KEY)

    print("\nJ.A.R.V.I.S. bereit.")
    if wake_word_mode:
        print("Modus: Wake Word – sag 'Hey JARVIS'")
    else:
        print("Modus: Manuell – ENTER zum Sprechen")
    print("─" * 50)

    history: list[dict] = []

    try:
        while True:
            set_state(State.IDLE)

            if wake_word_mode:
                print("\nHöre auf Wake Word...")
                audio.listen_for_wake_word(config.PICOVOICE_ACCESS_KEY)
                print("Ich höre... (Stille beendet Aufnahme)")
                set_state(State.LISTENING)
                wav_path = audio.record_with_vad()
            else:
                input("\nENTER zum Sprechen...")
                print("Aufnahme läuft... ENTER zum Stoppen.")
                set_state(State.LISTENING)
                wav_path = audio.record_until_enter()

            if not wav_path:
                print("[!] Keine Aufnahme erkannt.")
                continue

            print("Transkribiere...")
            user_text = stt.transcribe(wav_path)
            try:
                os.unlink(wav_path)
            except OSError:
                pass

            if not user_text:
                print("[!] Kein Text erkannt.")
                continue

            print(f"\nDu: {user_text}")
            history.append({"role": "user", "content": user_text})

            context.refresh_if_stale()
            system_prompt = context.build_system_prompt()

            set_state(State.THINKING)
            response = _run_with_streaming_tts(system_prompt, history)
            history.append({"role": "assistant", "content": response})
            history = history[-20:]

            print("─" * 50)

    except KeyboardInterrupt:
        print("\n\nJ.A.R.V.I.S. beendet.")
        sys.exit(0)


if __name__ == "__main__":
    main()
