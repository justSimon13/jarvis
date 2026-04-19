import subprocess
import tempfile
import numpy as np
import sounddevice as sd
import config

_elevenlabs_client = None
PCM_SAMPLERATE = 24000


def _get_elevenlabs():
    global _elevenlabs_client
    if _elevenlabs_client is None:
        from elevenlabs.client import ElevenLabs
        _elevenlabs_client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _elevenlabs_client


def speak(text: str):
    """Vollständigen Text sprechen (Fallback / kurze Texte)."""
    if config.ELEVENLABS_API_KEY:
        _speak_elevenlabs(text)
    else:
        _speak_native(text)


def speak_stream(text: str):
    """Text zu ElevenLabs streamen und PCM direkt über Sounddevice abspielen."""
    if not config.ELEVENLABS_API_KEY:
        _speak_native(text)
        return

    client = _get_elevenlabs()
    audio_stream = client.text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID,
        text=text,
        model_id="eleven_turbo_v2_5",
        output_format="pcm_24000",
    )

    with sd.OutputStream(samplerate=PCM_SAMPLERATE, channels=1, dtype="int16") as stream:
        for chunk in audio_stream:
            if chunk:
                pcm = np.frombuffer(chunk, dtype=np.int16)
                stream.write(pcm)


def _speak_elevenlabs(text: str):
    client = _get_elevenlabs()
    audio_stream = client.text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID,
        text=text,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    )
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    for chunk in audio_stream:
        tmp.write(chunk)
    tmp.flush()
    subprocess.run(["afplay", tmp.name], check=True)


def _speak_native(text: str):
    subprocess.run(["say", "-v", "Anna", text], check=True)
