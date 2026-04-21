import os
import sys
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


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TOOL_RUNNING = "tool_running"


_state = State.IDLE

SENTENCE_END = re.compile(r'([^.!?\n]{15,}[.!?\n]+)')
TTS_BUFFER_MIN = 120  # Mindestzeichen bevor ein Chunk abgeschickt wird


def set_state(s: State):
    global _state
    _state = s


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


def _run_with_streaming_tts(system_prompt: str, messages: list[dict]) -> str:
    """LLM streamen, Sätze buffern und gebündelt an ElevenLabs senden."""
    client_messages = messages.copy()
    full_response = ""

    while True:
        tts_queue = queue.Queue()
        turn_text = ""
        buffer = ""

        tts_done = threading.Event()
        tts_thread = threading.Thread(
            target=tts.speak_response, args=(tts_queue, tts_done), daemon=True
        )
        tts_thread.start()

        thinking_stop = threading.Event()
        thinking_thread = threading.Thread(
            target=audio.play_thinking_sound, args=(thinking_stop,), daemon=True
        )
        thinking_thread.start()

        print("J.A.R.V.I.S.: ", end="", flush=True)
        first_chunk_sent = False

        try:
            with llm.stream(system_prompt, client_messages, tools.DEFINITIONS) as s:
                for chunk in s.text_stream:
                    print(chunk, end="", flush=True)
                    buffer += chunk
                    turn_text += chunk
                    set_state(State.THINKING)
                    while True:
                        match = SENTENCE_END.search(buffer)
                        if match and match.end() >= TTS_BUFFER_MIN:
                            send = buffer[:match.end()].strip()
                            buffer = buffer[match.end():].lstrip()
                            if send:
                                if not first_chunk_sent:
                                    thinking_stop.set()
                                    first_chunk_sent = True
                                set_state(State.SPEAKING)
                                tts_queue.put(send)
                        else:
                            break
                final = s.get_final_message()
        except anthropic.APIStatusError as e:
            thinking_stop.set()
            tts_queue.put(None)
            if "overloaded" in str(e).lower():
                print("\n[!] Anthropic überlastet, warte 5s...")
                time.sleep(5)
                tts.speak("Entschuldigung Sir, die Server sind kurz überlastet. Bitte wiederholen Sie Ihre Anfrage.")
            else:
                print(f"\n[!] API-Fehler: {e}")
                tts.speak("Es gab einen API-Fehler, Sir.")
            return ""
        except Exception as e:
            thinking_stop.set()
            tts_queue.put(None)
            print(f"\n[!] Unerwarteter Fehler: {e}")
            return ""

        thinking_stop.set()
        if buffer.strip():
            tts_queue.put(buffer.strip())
        tts_queue.put(None)
        tts_done.wait()
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
                    print(f"[tool] → {_compress_tool_result(block.name, result)}")
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

    wake_word_mode = not config.MANUAL_MODE

    print("\nJ.A.R.V.I.S. bereit.")
    if wake_word_mode:
        print("Modus: Wake Word – sag 'Hey JARVIS'")
    else:
        print("Modus: Manuell – ENTER zum Sprechen")
    print("─" * 50)

    tts.speak("Alle Systeme bereit, Sir.")

    history: list[dict] = []
    in_conversation = False
    silent_turns = 0
    MAX_SILENT_TURNS = 2  # nach 2 leeren Aufnahmen → zurück zum Wake Word

    try:
        while True:
            set_state(State.IDLE)

            if wake_word_mode and not in_conversation:
                print("\nHöre auf Wake Word...")
                audio.listen_for_wake_word()

            if wake_word_mode:
                print("Ich höre... (Stille beendet Aufnahme)")
            else:
                input("\nENTER zum Sprechen...")
                print("Aufnahme läuft... ENTER zum Stoppen.")

            set_state(State.LISTENING)
            wav_path = audio.record_with_vad() if wake_word_mode else audio.record_until_enter()

            if not wav_path:
                print("[!] Keine Aufnahme erkannt.")
                silent_turns += 1
                if silent_turns >= MAX_SILENT_TURNS:
                    in_conversation = False
                    silent_turns = 0
                continue

            print("Transkribiere...")
            user_text = stt.transcribe(wav_path)
            try:
                os.unlink(wav_path)
            except OSError:
                pass

            if not user_text:
                print("[!] Kein Text erkannt.")
                silent_turns += 1
                if silent_turns >= MAX_SILENT_TURNS:
                    in_conversation = False
                    silent_turns = 0
                continue

            silent_turns = 0
            in_conversation = True

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
