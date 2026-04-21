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
VAD_BLOCKSIZE = 1024
VAD_SILENCE_THRESHOLD = 0.02  # RMS unter diesem Wert gilt als Stille
VAD_SILENCE_SECONDS = 1.5     # Stille-Dauer bis Stop
VAD_MAX_SECONDS = 15          # Maximale Aufnahmedauer


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


def record_with_vad() -> str:
    """Nimmt auf und stoppt automatisch nach Stille."""
    frames = []
    silent_blocks = 0
    speaking_started = False
    stop_event = threading.Event()

    silence_block_limit = int(VAD_SILENCE_SECONDS * SAMPLE_RATE / VAD_BLOCKSIZE)

    def callback(indata, frame_count, time_info, status):
        nonlocal silent_blocks, speaking_started
        frames.append(indata.copy())
        rms = float(np.sqrt(np.mean(indata ** 2)))

        if rms > VAD_SILENCE_THRESHOLD:
            speaking_started = True
            silent_blocks = 0
        elif speaking_started:
            silent_blocks += 1
            if silent_blocks >= silence_block_limit:
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
