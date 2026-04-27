import subprocess
import sys
import tempfile
import threading
import numpy as np
import config

_elevenlabs_client = None
PCM_SAMPLERATE = 24000


def _get_elevenlabs():
    global _elevenlabs_client
    if _elevenlabs_client is None:
        from elevenlabs.client import ElevenLabs
        _elevenlabs_client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _elevenlabs_client


def _voice_settings():
    from elevenlabs.types import VoiceSettings
    return VoiceSettings(
        stability=0.35,
        similarity_boost=0.75,
        style=0.45,
        use_speaker_boost=True,
    )


def speak(text: str):
    """Vollständigen Text sprechen (Startup-Meldungen, kurze Texte)."""
    if config.ELEVENLABS_API_KEY:
        _speak_elevenlabs(text)
    else:
        _speak_native(text)


def speak_response(text_queue, done_event, audio_callback=None):
    """
    Liest Text-Chunks aus text_queue, ruft ElevenLabs auf und gibt Audio aus.
    Beendet wenn None in text_queue kommt.

    audio_callback=None    : lokal abspielen via sounddevice (standalone)
    audio_callback=callable: PCM-Bytes an Callback übergeben (server/pipeline)
    """
    if not config.ELEVENLABS_API_KEY:
        chunks = []
        while True:
            t = text_queue.get()
            if t is None:
                break
            chunks.append(t)
        if chunks:
            _speak_native(" ".join(chunks))
        done_event.set()
        return

    client = _get_elevenlabs()

    if audio_callback:
        # Callback-Modus: ElevenLabs → callback(pcm_bytes)
        while True:
            text = text_queue.get()
            if text is None:
                break
            try:
                audio_stream = client.text_to_speech.convert(
                    voice_id=config.ELEVENLABS_VOICE_ID,
                    text=text,
                    model_id="eleven_turbo_v2_5",
                    output_format="pcm_24000",
                    voice_settings=_voice_settings(),
                )
                for chunk in audio_stream:
                    if chunk:
                        audio_callback(chunk)
            except Exception as e:
                print(f"[tts] Fehler: {e}")
        done_event.set()

    else:
        # Lokaler Modus: ElevenLabs → interner Queue → persistenter OutputStream
        import sounddevice as sd

        internal_queue = _Queue()
        _SENTINEL = object()

        def fetch_worker():
            while True:
                text = text_queue.get()
                if text is None:
                    internal_queue.put(_SENTINEL)
                    return
                try:
                    audio_stream = client.text_to_speech.convert(
                        voice_id=config.ELEVENLABS_VOICE_ID,
                        text=text,
                        model_id="eleven_turbo_v2_5",
                        output_format="pcm_24000",
                        voice_settings=_voice_settings(),
                    )
                    for chunk in audio_stream:
                        if chunk:
                            internal_queue.put(chunk)
                except Exception as e:
                    print(f"[tts] Fehler: {e}")

        fetcher = threading.Thread(target=fetch_worker, daemon=True)
        fetcher.start()

        with sd.OutputStream(samplerate=PCM_SAMPLERATE, channels=1, dtype="int16") as stream:
            while True:
                chunk = internal_queue.get()
                if chunk is _SENTINEL:
                    break
                stream.write(np.frombuffer(chunk, dtype=np.int16))

        fetcher.join()
        done_event.set()


def _speak_elevenlabs(text: str):
    client = _get_elevenlabs()
    audio_stream = client.text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID,
        text=text,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
        voice_settings=_voice_settings(),
    )
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    for chunk in audio_stream:
        tmp.write(chunk)
    tmp.flush()
    if sys.platform == "darwin":
        subprocess.run(["afplay", tmp.name], check=True)
    else:
        subprocess.run(["aplay", tmp.name], stderr=subprocess.DEVNULL)


def _speak_native(text: str):
    if sys.platform == "darwin":
        subprocess.run(["say", "-v", "Anna", text], check=True)
    else:
        subprocess.run(["espeak", "-v", "de", text], stderr=subprocess.DEVNULL)


# queue.Queue Alias um Import oben zu vermeiden
import queue as _queue_module
_Queue = _queue_module.Queue
