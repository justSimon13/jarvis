import subprocess
import tempfile
import threading
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav

SAMPLE_RATE = 16000
VAD_BLOCKSIZE = 1024
VAD_SILENCE_THRESHOLD = 0.02  # RMS unter diesem Wert gilt als Stille
VAD_SILENCE_SECONDS = 1.5     # Stille-Dauer bis Stop
VAD_MAX_SECONDS = 15          # Maximale Aufnahmedauer


def listen_for_wake_word(access_key: str):
    """Blockiert bis 'Hey JARVIS' erkannt wird."""
    import pvporcupine
    porcupine = pvporcupine.create(access_key=access_key, keywords=["jarvis"])

    detected = threading.Event()
    frame_buffer = []

    def callback(indata, frames, time_info, status):
        pcm = (indata[:, 0] * 32767).astype(np.int16).tolist()
        frame_buffer.extend(pcm)
        while len(frame_buffer) >= porcupine.frame_length:
            frame = frame_buffer[:porcupine.frame_length]
            del frame_buffer[:porcupine.frame_length]
            if porcupine.process(frame) >= 0:
                detected.set()

    with sd.InputStream(
        samplerate=porcupine.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=porcupine.frame_length,
        callback=callback,
    ):
        detected.wait()

    porcupine.delete()
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
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
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
    subprocess.run(["afplay", path], check=True)


def _beep():
    """Kurzer Ton als Bestätigung dass Wake Word erkannt wurde."""
    subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"])
