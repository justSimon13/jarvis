import whisper
import numpy as np
import scipy.io.wavfile as wav
import config

_model = None
_MIN_RMS = 0.01   # unter diesem Wert gilt die Aufnahme als zu leise
_MIN_SECONDS = 0.5


def load_model():
    global _model
    if _model is None:
        print(f"[stt] Lade Whisper-Modell '{config.WHISPER_MODEL}'...")
        _model = whisper.load_model(config.WHISPER_MODEL)


def transcribe(wav_path: str) -> str:
    # Leere oder zu leise Aufnahmen vor Whisper abfangen
    try:
        rate, data = wav.read(wav_path)
        if data.size == 0 or len(data) / rate < _MIN_SECONDS:
            return ""
        rms = float(np.sqrt(np.mean((data.astype(np.float32) / 32768) ** 2)))
        if rms < _MIN_RMS:
            return ""
    except Exception:
        pass

    load_model()
    result = _model.transcribe(wav_path, language="de")
    return result["text"].strip()
