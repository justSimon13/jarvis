import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
MANUAL_MODE = os.getenv("MANUAL_MODE", "false").lower() == "true"
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "")
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "")
EMAIL_SEND_ENABLED = os.getenv("EMAIL_SEND_ENABLED", "false").lower() == "true"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE")  # None = System-Default
WEATHER_CITY = os.getenv("WEATHER_CITY", "Stuttgart")
JARVIS_SERVER = os.getenv("JARVIS_SERVER", "")  # z.B. "ws://100.x.x.x:8765" — leer = Standalone

VERSION = "1.2.0"
GITHUB_REPO = "justSimon13/jarvis"

JARVIS_DIR = Path.home() / ".jarvis"
JARVIS_DIR.mkdir(exist_ok=True)

NOTION_CACHE_DB   = JARVIS_DIR / "notion_cache.db"
KNOWLEDGE_DIR     = JARVIS_DIR / "knowledge"
KNOWLEDGE_INDEX_DB = JARVIS_DIR / "knowledge_index.db"
TRACKING_DB       = JARVIS_DIR / "tracking.db"

NOTION_TODOS_DB_ID = os.getenv("NOTION_TODOS_DB_ID", "10ab63fa-fc26-80f5-9865-cf57555d8002")
NOTION_PROJEKTE_DB_ID = os.getenv("NOTION_PROJEKTE_DB_ID", "194b63fa-fc26-80d1-9832-dceb4301afd3")
NOTION_KONZEPTE_DB_ID = os.getenv("NOTION_KONZEPTE_DB_ID", "19fb63fa-fc26-80d3-807c-ffba582e38c0")
NOTION_KONTAKTE_DB_ID = os.getenv("NOTION_KONTAKTE_DB_ID", "1a4b63fa-fc26-808c-ad83-e4973e38f570")
NOTION_CACHE_TTL = 15 * 60  # seconds
