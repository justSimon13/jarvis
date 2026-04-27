"""
WebSocket-Protokoll für JARVIS Server ↔ Client Kommunikation.

Binary frames:
  Client → Server: WAV-Audiodaten (aufgenommene Sprache)
  Server → Client: PCM-Audiodaten (TTS, 24000 Hz, Mono, int16)

JSON frames: alle anderen Nachrichten (State, Text, Events)
"""

# Audio-Format (PCM, Server → Client)
PCM_SAMPLERATE = 24000
PCM_CHANNELS = 1
PCM_DTYPE = "int16"

# ── Server → Client ───────────────────────────────────────────────────────────
STATE = "state"               # {"type": "state", "state": "idle|listening|thinking|speaking|tool_running"}
STATUS = "status"             # {"type": "status", "text": "..."}
TRANSCRIPT = "transcript"     # {"type": "transcript", "text": "..."} — erkannter Text
RESPONSE_START = "response_start"  # {"type": "response_start"}
RESPONSE_CHUNK = "response_chunk"  # {"type": "response_chunk", "text": "..."}
RESPONSE_DONE = "response_done"    # {"type": "response_done", "text": "..."}
TOOL = "tool"                 # {"type": "tool", "name": "..."}
ERROR = "error"               # {"type": "error", "message": "..."}
PONG = "pong"                 # {"type": "pong"}

# ── Client → Server ───────────────────────────────────────────────────────────
TEXT_INPUT = "text_input"     # {"type": "text_input", "text": "...", "tts": bool}
                              #   tts=True  → LLM + TTS (Voice-Mode, Default)
                              #   tts=False → LLM only, kein Audio (Text-Mode)
PING = "ping"                 # {"type": "ping"}
