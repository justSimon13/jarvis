import numpy as np
import scipy.io.wavfile as wav
from elevenlabs.client import ElevenLabs
import config

_client = None
_MIN_RMS = 0.01
_MIN_SECONDS = 0.5


def _get_client() -> ElevenLabs:
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _client


def load_model():
    pass


def transcribe(wav_path: str) -> str:
    try:
        rate, data = wav.read(wav_path)
        if data.size == 0 or len(data) / rate < _MIN_SECONDS:
            return ""
        rms = float(np.sqrt(np.mean((data.astype(np.float32) / 32768) ** 2)))
        if rms < _MIN_RMS:
            return ""
    except Exception:
        pass

    with open(wav_path, "rb") as f:
        result = _get_client().speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            language_code="de",
        )
    return result.text.strip()
