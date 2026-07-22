import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
MANUAL_MODE = os.getenv("MANUAL_MODE", "false").lower() == "true"  # main.py Standalone-CLI: Wake-Word vs. manuelles ENTER
CODING_ENGINE_API_KEY = os.getenv("CODING_ENGINE_API_KEY", ANTHROPIC_API_KEY)  # eigener Key optional, sonst Fallback
CODING_MANUAL_MODE = os.getenv("CODING_MANUAL_MODE", "false").lower() == "true"  # Coding-Engine: Freigabe bei JEDER Aktion statt nur bei riskanten
CODING_TASK_BUDGET_USD = float(os.getenv("CODING_TASK_BUDGET_USD", "3.0"))    # Harte Obergrenze pro einzelnem Coding-Task
CODING_DAILY_BUDGET_USD = float(os.getenv("CODING_DAILY_BUDGET_USD", "10.0"))  # Tages-Gesamtlimit für die Coding-Engine
CODING_ENGINE_MODEL = os.getenv("CODING_ENGINE_MODEL", "claude-sonnet-5")       # Default: günstiger
CODING_ENGINE_MODEL_HIGH = os.getenv("CODING_ENGINE_MODEL_HIGH", "claude-opus-4-8")  # nur wenn explizit "mehr Power" verlangt
CHAT_DAILY_BUDGET_USD = float(os.getenv("CHAT_DAILY_BUDGET_USD", "5.0"))  # Nur Sichtbarkeit/Warnung, kein Hard-Block wie bei der Coding-Engine
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # für PR-Erstellung durch die Coding-Engine (services/coding_engine.py)
PROJECTS_ROOT = Path.home() / "apps"  # begrenzter Sandbox-Ordner für create_project (services/coding_engine.py) — gleicher Ort wie der bestehende jarvis-web-Checkout

JARVIS_DIR = Path.home() / ".jarvis"
JARVIS_DIR.mkdir(exist_ok=True)

KNOWLEDGE_DIR     = JARVIS_DIR / "knowledge"
KNOWLEDGE_INDEX_DB = JARVIS_DIR / "knowledge_index.db"
TRACKING_DB       = JARVIS_DIR / "tracking.db"
