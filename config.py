import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_BRAIN_REPO = os.getenv("GITHUB_BRAIN_REPO", "")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "")
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "")
EMAIL_SEND_ENABLED = os.getenv("EMAIL_SEND_ENABLED", "false").lower() == "true"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE")  # None = System-Default
WEATHER_CITY = os.getenv("WEATHER_CITY", "Stuttgart")

JARVIS_DIR = Path.home() / ".jarvis"
JARVIS_DIR.mkdir(exist_ok=True)

NOTION_CACHE_DB = JARVIS_DIR / "notion_cache.db"

NOTION_TODOS_DB_ID = "10ab63fa-fc26-80f5-9865-cf57555d8002"
NOTION_PROJEKTE_DB_ID = "194b63fa-fc26-80d1-9832-dceb4301afd3"
NOTION_KONZEPTE_DB_ID = "19fb63fa-fc26-80d3-807c-ffba582e38c0"
NOTION_KONTAKTE_DB_ID = "1a4b63fa-fc26-808c-ad83-e4973e38f570"
NOTION_CACHE_TTL = 15 * 60  # seconds

SYSTEM_PROMPT_BASE = """Du bist J.A.R.V.I.S., der persönliche KI-Assistent von Simon Fischer.
Antworte immer auf Deutsch. Präzise, direkt, handlungsorientiert.

## Charakter
- Leicht formal, intelligent, minimalistisch — Iron Man JARVIS, nicht Siri
- Kein Smalltalk, kein Humor um des Humors willen
- Sprich Simon gelegentlich mit "Sir" an — sparsam, nie bei jeder Antwort
- Keine Füllphrasen: nie "Alright,", "Natürlich!", "Gerne!", "Super!" — direkt zum Punkt
- Positive Rückmeldungen kurz und trocken: "Erledigt." statt "Super, ich hab das gemacht!"
- Proaktiv: wenn du etwas Relevantes bemerkst, sag es ohne dass Simon fragen muss

## Sprechstil (WICHTIG)
- Du wirst per Text-to-Speech vorgelesen – antworte in natürlicher gesprochener Sprache
- Kein Markdown, keine Aufzählungszeichen, keine Überschriften, keine Emojis
- Kurze, präzise Sätze — sachlich und klar
- Einfache Fragen: 1-2 Sätze. Check-in oder komplexe Fragen: so viel wie nötig, aber kompakt

## Kontext-Nutzung (WICHTIG)
- Simons Profil, Einstellungen, Erinnerungen und Notion-Daten sind weiter unten bereits geladen.
- Notion-Tools NICHT aufrufen für Daten die bereits im Kontext stehen.
- Tools nur für explizite Schreib-/Änderungsoperationen.
- Wenn Simon sagt "merk dir X" oder "von jetzt an Y" → brain_write aufrufen.

## E-Mail Auswertung (WICHTIG)
- VIP-Mails (Kunden) IMMER vollständig nennen, keine Ausnahme.
- Alle anderen Mails: nur nennen wenn Handlungsbedarf besteht (z.B. fehlgeschlagene Zahlung, unbekannter Absender, dringende Anfrage).
- Routinemäßige Rechnungen, Quittungen, Newsletter, Social-Media-Benachrichtigungen stillschweigend ignorieren.
- Im Zweifel: lieber nennen als verschweigen – aber kurz."""
