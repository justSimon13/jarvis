"""
J.A.R.V.I.S. Standalone — Entwicklung & Fallback (Terminal, kein Server nötig).
Nutzt JarvisPipeline direkt mit lokaler Audio-Wiedergabe.

Start: python3 main.py
"""
import os
import queue
import signal
import sys
import threading

import audio
import brain
import config
import context
import protocol as P
import stt
import tts
from pipeline import JarvisPipeline


# ── Lokale Audio-Wiedergabe ───────────────────────────────────────────────────

class _LocalPlayer:
    """Lazy-open Playback-Thread: OutputStream nur während Wiedergabe offen.
    Verhindert Core Audio-Konflikte mit dem InputStream beim Wake-Word-Hören."""

    _STOP = object()
    _IDLE_TIMEOUT = 0.3  # Sekunden Stille bis Stream geschlossen wird

    def __init__(self):
        import sounddevice as sd
        import numpy as np
        self._sd = sd
        self._np = np
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            # Warte auf ersten Chunk — kein OutputStream offen während Idle
            chunk = self._q.get()
            if chunk is self._STOP:
                return

            with self._sd.OutputStream(
                samplerate=tts.PCM_SAMPLERATE, channels=1, dtype="int16"
            ) as stream:
                stream.write(self._np.frombuffer(chunk, dtype=self._np.int16))
                while True:
                    try:
                        chunk = self._q.get(timeout=self._IDLE_TIMEOUT)
                        if chunk is self._STOP:
                            return
                        stream.write(self._np.frombuffer(chunk, dtype=self._np.int16))
                    except queue.Empty:
                        break  # Keine weiteren Chunks → Stream schließen

    def write(self, pcm_bytes: bytes):
        self._q.put(pcm_bytes)


# ── Event-Handler ─────────────────────────────────────────────────────────────

_thinking_stop: threading.Event | None = None


def _on_event(event: dict):
    global _thinking_stop
    t = event.get("type")

    if t == P.STATE:
        state = event.get("state", "")
        if state == "thinking" and _thinking_stop is None:
            _thinking_stop = threading.Event()
            threading.Thread(
                target=audio.play_thinking_sound, args=(_thinking_stop,), daemon=True
            ).start()
        elif state in ("speaking", "idle", "tool_running") and _thinking_stop:
            _thinking_stop.set()
            _thinking_stop = None

    elif t == P.STATUS:
        print(f"\r[{event.get('text', '')}]          ", end="", flush=True)

    elif t == P.TRANSCRIPT:
        print(f"\nDu:       {event.get('text', '')}", flush=True)

    elif t == P.RESPONSE_START:
        print("\nJ.A.R.V.I.S.: ", end="", flush=True)

    elif t == P.RESPONSE_CHUNK:
        print(event.get("text", ""), end="", flush=True)

    elif t == P.RESPONSE_DONE:
        print()

    elif t == P.TOOL:
        print(f"\n[tool]    {event.get('name', '')}", flush=True)

    elif t == P.ERROR:
        print(f"\n[!]       {event.get('message', '')}", flush=True)


# ── Haupt-Loop ────────────────────────────────────────────────────────────────

def _print_audio_devices():
    import sounddevice as sd
    print("[audio] Verfügbare Input-Geräte:")
    for i, info in enumerate(sd.query_devices()):
        ch = info.get("max_input_channels", 0)
        if ch > 0:
            print(f"  [{i}] {info['name']}  (in={ch}, {int(info['default_samplerate'])}Hz)")
    default = sd.query_devices(kind="input")
    print(f"[audio] System-Default Input: {default['name']!r}", flush=True)


def main():
    print("J.A.R.V.I.S. startet...")
    _print_audio_devices()

    brain.sync()
    context.refresh_if_stale()
    stt.load_model()

    player = _LocalPlayer()
    pipeline = JarvisPipeline(
        client_id="local",
        on_event=_on_event,
        on_audio=player.write,
    )

    def shutdown(sig=None, frame=None):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            pipeline.save_session()
        except Exception:
            pass
        print("\nAuf Wiedersehen, Sir.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)

    tts.speak("Alle Systeme bereit, Sir.")

    in_conversation = False
    silent_turns = 0
    MAX_SILENT_TURNS = 1

    print("\nJ.A.R.V.I.S. bereit.")
    if not config.MANUAL_MODE:
        print("Modus: Wake Word – sag 'Hey JARVIS'")
    else:
        print("Modus: Manuell – ENTER zum Sprechen")
    print("─" * 50)

    try:
        while True:
            if not config.MANUAL_MODE and not in_conversation:
                print("\nHöre auf Wake Word...")
                audio.listen_for_wake_word()
                brain.check_missed_routines()
                context.refresh_if_stale()

            if not config.MANUAL_MODE:
                print("Ich höre...")
            else:
                input("\nENTER zum Sprechen...")
                print("Aufnahme läuft...")

            wav_path = (
                audio.record_with_vad() if not config.MANUAL_MODE
                else audio.record_until_enter()
            )

            if not wav_path:
                silent_turns += 1
                if silent_turns >= MAX_SILENT_TURNS:
                    if in_conversation:
                        pipeline.save_session()
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
                silent_turns += 1
                if silent_turns >= MAX_SILENT_TURNS:
                    if in_conversation:
                        pipeline.save_session()
                    in_conversation = False
                    silent_turns = 0
                continue

            silent_turns = 0
            in_conversation = True
            pipeline.process_text(user_text, use_tts=True)
            import time; time.sleep(0.6)  # Audio aushalten lassen bevor Mic öffnet
            print("─" * 50)

    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
