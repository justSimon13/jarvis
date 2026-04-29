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
CLIENT_HELLO = "client_hello" # {"type": "client_hello", "name": "schlafzimmer"}
ALARM_SYNC   = "alarm_sync"   # {"type": "alarm_sync", "alarms": [...]} — Client → Server beim Connect

# ── Alarm-Steuerung (Server → Client) ─────────────────────────────────────────
SET_ALARM    = "set_alarm"    # {"type": "set_alarm", "alarm_id": "...", "hour": H, "minute": M, "label": "...", "snooze_minutes": N, "max_snooze": N, "song": "..."|null}
CANCEL_ALARM = "cancel_alarm" # {"type": "cancel_alarm", "alarm_id": "..."|null}
SNOOZE_ALARM = "snooze_alarm" # {"type": "snooze_alarm", "alarm_id": "..."|null, "minutes": N}
PLAY_MUSIC   = "play_music"   # {"type": "play_music", "song": "query", "volume": 70}
STOP_MUSIC   = "stop_music"   # {"type": "stop_music"}
