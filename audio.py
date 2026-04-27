import queue
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
    return int(v) if v else None


def _input_channels() -> int:
    """Gibt die tatsächlich unterstützte Kanalanzahl des Input-Geräts zurück (min 1, max 2)."""
    try:
        info = sd.query_devices(_input_device(), "input")
        ch = int(info["max_input_channels"])
        print(f"[audio] Input device: {info['name']!r}  max_input_channels={ch}", flush=True)
        return min(2, max(1, ch))
    except Exception as e:
        print(f"[audio] _input_channels fallback (err: {e})", flush=True)
        return 1


_INPUT_BLACKLIST = ("iphone", "ipad", "teams", "eqmac")
_INPUT_PREFER = ("macbook", "mikrofon", "microphone", "kopfhörer", "headphone")


def _rank_input_device(name: str) -> int:
    """Niedrigerer Rang = höhere Priorität."""
    n = name.lower()
    if any(b in n for b in _INPUT_BLACKLIST):
        return 99
    for i, p in enumerate(_INPUT_PREFER):
        if p in n:
            return i
    return 50


def _open_input_stream(samplerate: int, blocksize: int, callback) -> sd.InputStream:
    """Öffnet InputStream. Explizit konfiguriertes Gerät zuerst, sonst nach Präferenz sortiert."""
    explicit = _input_device()
    if explicit is not None:
        candidates: list[int] = [explicit]
    else:
        # Alle echten Input-Geräte nach Präferenz sortiert
        devs = [
            (i, info) for i, info in enumerate(sd.query_devices())
            if info.get("max_input_channels", 0) > 0
        ]
        devs.sort(key=lambda x: _rank_input_device(x[1]["name"]))
        candidates = [i for i, _ in devs]

    for device in candidates:
        try:
            dev_info = sd.query_devices(device)
            ch = max(1, min(2, int(dev_info.get("max_input_channels", 1))))
        except Exception:
            ch = 1
        try:
            stream = sd.InputStream(
                samplerate=samplerate,
                channels=ch,
                dtype="float32",
                blocksize=blocksize,
                callback=callback,
                device=device,
            )
            print(f"[audio] InputStream: [{device}] {dev_info.get('name', '?')!r} ch={ch}", flush=True)
            return stream
        except sd.PortAudioError as e:
            print(f"[audio] [{device}] fehlgeschlagen: {e}", flush=True)
    raise sd.PortAudioError("InputStream: kein Input-Gerät funktioniert")

SAMPLE_RATE = 16000
VAD_BLOCKSIZE = 512           # Silero VAD benötigt 512 samples @ 16kHz
VAD_MAX_SECONDS = 30          # Maximale Aufnahmedauer

# Silero VAD: Schwelle + Stille-Dauer (viel kürzer möglich da neural)
_SILERO_THRESHOLD = 0.4       # Sprach-Wahrscheinlichkeit ab der als Sprache gilt
_SILENCE_MS = 1500            # ms Stille bis VAD "end" feuert — Engine akkumuliert danach weiter

_silero_model = None
_silero_lock = threading.Lock()
_oww_model = None
_oww_lock = threading.Lock()


def _get_silero():
    global _silero_model
    with _silero_lock:
        if _silero_model is None:
            from silero_vad import load_silero_vad, VADIterator  # noqa
            _silero_model = load_silero_vad()
    return _silero_model


def _get_oww():
    global _oww_model
    with _oww_lock:
        if _oww_model is None:
            from openwakeword.model import Model
            _oww_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    return _oww_model


def listen_for_wake_word(interrupt: threading.Event | None = None):
    """Blockiert bis 'Hey JARVIS' erkannt wird oder interrupt gesetzt wird."""
    oww = _get_oww()
    oww.reset()  # State zwischen Sessions leeren

    detected = threading.Event()
    pcm_q: queue.Queue = queue.Queue(maxsize=20)
    chunk_size = 1280  # 80ms @ 16kHz

    # Callback läuft im PortAudio-Realtime-Thread — nur Daten queuen, KEIN ONNX hier.
    # ONNX RT im Realtime-Thread führt zu unbegrenztem Thread-local-Heap-Wachstum.
    def audio_callback(indata, frames, time_info, status):
        try:
            pcm_q.put_nowait((indata[:, 0] * 32767).astype(np.int16).copy())
        except queue.Full:
            pass

    def inference_worker():
        while not detected.is_set():
            try:
                pcm = pcm_q.get(timeout=0.1)
                scores = oww.predict(pcm)
                if scores.get("hey_jarvis", 0) >= 0.35:
                    detected.set()
            except queue.Empty:
                pass

    infer_thread = threading.Thread(target=inference_worker, daemon=True)
    infer_thread.start()

    with _open_input_stream(SAMPLE_RATE, chunk_size, audio_callback):
        while not detected.is_set():
            if interrupt and interrupt.is_set():
                detected.set()
                break
            detected.wait(timeout=0.2)

    infer_thread.join(timeout=1.0)
    _beep()
    import time
    time.sleep(0.3)


def record_with_vad(interrupt: threading.Event | None = None) -> str:
    """Nimmt auf und stoppt via Silero VAD bei Redepause. interrupt bricht sofort ab."""
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
    stop_event = threading.Event()
    pcm_q: queue.Queue = queue.Queue(maxsize=200)

    # Callback läuft im PortAudio-Realtime-Thread — nur Daten queuen, kein PyTorch hier.
    def audio_callback(indata, frame_count, time_info, status):
        try:
            pcm_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def vad_worker():
        speaking_started = False
        while not stop_event.is_set():
            try:
                chunk = pcm_q.get(timeout=0.1)
                frames.append(chunk)
                result = vad(torch.from_numpy(chunk))
                if result is not None:
                    if "start" in result:
                        speaking_started = True
                    elif "end" in result and speaking_started:
                        stop_event.set()
            except queue.Empty:
                pass

    vad_thread = threading.Thread(target=vad_worker, daemon=True)
    vad_thread.start()

    with _open_input_stream(SAMPLE_RATE, VAD_BLOCKSIZE, audio_callback):
        while not stop_event.is_set():
            if interrupt and interrupt.is_set():
                stop_event.set()
                break
            stop_event.wait(timeout=0.2)

    vad_thread.join(timeout=1.0)
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
