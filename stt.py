import whisper
import config

_model = None


def load_model():
    global _model
    if _model is None:
        print(f"[stt] Lade Whisper-Modell '{config.WHISPER_MODEL}'...")
        _model = whisper.load_model(config.WHISPER_MODEL)


def transcribe(wav_path: str) -> str:
    load_model()
    result = _model.transcribe(wav_path, language="de")
    return result["text"].strip()
