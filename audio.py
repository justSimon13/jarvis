import subprocess
import sys
import tempfile
import threading
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav
import config

def _input_device():
    v = config.AUDIO_INPUT_DEVICE
    return int(v) if v is not None else None

SAMPLE_RATE = 16000
VAD_BLOCKSIZE = 512           # Silero VAD benötigt 512 samples @ 16kHz
VAD_MAX_SECONDS = 30          # Maximale Aufnahmedauer

# Silero VAD: Schwelle + Stille-Dauer (viel kürzer möglich da neural)
_SILERO_THRESHOLD = 0.4       # Sprach-Wahrscheinlichkeit ab der als Sprache gilt
_SILENCE_MS = 800             # ms Stille bis Stop (vs 2500ms bei RMS)

_silero_model = None
_silero_lock = threading.Lock()


def _get_silero():
    global _silero_model
    with _silero_lock:
        if _silero_model is None:
            from silero_vad import load_silero_vad, VADIterator  # noqa
            _silero_model = load_silero_vad()
    return _silero_model


def listen_for_wake_word():
    """Blockiert bis 'Hey JARVIS' erkannt wird (openwakeword, kein API-Key)."""
    from openwakeword.model import Model

    oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    detected = threading.Event()
    chunk_size = 1280  # 80ms @ 16kHz

    def callback(indata, frames, time_info, status):
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        scores = oww.predict(pcm)
        if scores.get("hey_jarvis", 0) >= 0.5:
            detected.set()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=chunk_size,
        callback=callback,
        device=_input_device(),
    ):
        detected.wait()

    _beep()
    import time
    time.sleep(0.3)  # Beep abklingen lassen bevor Aufnahme startet


def record_with_vad() -> str:
    """Nimmt auf und stoppt via Silero VAD bei echtem Redepausen-Ende."""
    import torch
    from silero_vad import VADIterator

    model = _get_silero()
    vad = VADIterator(
        model,
        sampling_rate=SAMPLE_RATE,
        threshold=_SILERO_THRESHOLD,
        min_silence_duration_ms=_SILENCE_MS,
    )

    frames = []
    speaking_started = False
    stop_event = threading.Event()

    def callback(indata, frame_count, time_info, status):
        nonlocal speaking_started
        chunk = indata[:, 0].copy()
        frames.append(chunk)

        tensor = torch.from_numpy(chunk)
        result = vad(tensor)

        if result is not None:
            if "start" in result:
                speaking_started = True
            elif "end" in result and speaking_started:
                stop_event.set()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=VAD_BLOCKSIZE,
        callback=callback,
        device=_input_device(),
    ):
        stop_event.wait(timeout=VAD_MAX_SECONDS)

    vad.reset_states()

    if not frames:
        return ""

    audio = np.concatenate(frames, axis=0)
    audio_int16 = (audio * 32767).astype(np.int16)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.write(tmp.name, SAMPLE_RATE, audio_int16)
    return tmp.name


def record_until_enter() -> str:
    """Fallback: Aufnahme manuell per Enter stoppen."""
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback,
        device=_input_device(),
    )
    stream.start()
    input("")
    stream.stop()
    stream.close()

    if not frames:
        return ""

    audio = np.concatenate(frames, axis=0)
    audio_int16 = (audio * 32767).astype(np.int16)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.write(tmp.name, SAMPLE_RATE, audio_int16)
    return tmp.name


def play_mp3(path: str):
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=True)
    else:
        subprocess.run(["aplay", path], stderr=subprocess.DEVNULL)


def play_thinking_sound(stop_event: threading.Event):
    """Sanfter pulsierender Ton der loopt bis stop_event gesetzt wird."""
    sample_rate = 24000
    duration = 1.5
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Sinus bei 220 Hz, Lautstärke pulsiert langsam
    envelope = 0.3 + 0.2 * np.sin(2 * np.pi * 0.8 * t)
    tone = (envelope * np.sin(2 * np.pi * 220 * t) * 0.15).astype(np.float32)

    with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
        while not stop_event.is_set():
            stream.write(tone)


def _beep():
    if sys.platform == "darwin":
        subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"])
    # kein Beep auf Linux — kein Blocker
