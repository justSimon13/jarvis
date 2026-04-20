# J.A.R.V.I.S.

Persönlicher KI-Sprachassistent für macOS. Hört auf dein Wake Word, versteht Deutsch, antwortet per Sprache und hat Zugriff auf Kalender, E-Mail, Notion, Bitcoin-Kurs und Einkaufsliste.

---

## Voraussetzungen

- Python 3.11+
- macOS (Linux/Raspberry Pi experimentell unterstützt)
- API Keys: Anthropic, ElevenLabs, Notion, Picovoice (optional), GitHub

---

## Setup

```bash
git clone https://github.com/justSimon13/j.a.r.v.i.s..git
cd j.a.r.v.i.s.
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`.env` anlegen:

```env
ANTHROPIC_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
NOTION_API_KEY=...
PICOVOICE_ACCESS_KEY=...       # optional, für Wake Word
GITHUB_TOKEN=...
GITHUB_BRAIN_REPO=justSimon13/j.a.r.v.i.s.
EMAIL_ADDRESS=...
EMAIL_PASSWORD=...
EMAIL_IMAP_HOST=imap.ionos.de
EMAIL_SMTP_HOST=smtp.ionos.de
EMAIL_SEND_ENABLED=false       # auf true setzen um Mails zu versenden
WHISPER_MODEL=base
```

**Google Calendar einrichten** (einmalig):
1. Google Cloud Console → Projekt erstellen → Google Calendar API aktivieren
2. OAuth 2.0 Client ID erstellen (Desktop App) → JSON herunterladen → `~/.jarvis/google_credentials.json`
3. `python3 setup_google.py`

**Apple Reminders freischalten** (einmalig):
Systemeinstellungen → Datenschutz & Sicherheit → Automatisierung → Terminal → Reminders aktivieren

---

## Starten

```bash
python3 main.py
```

- **Wake Word Modus**: sag "Hey JARVIS" (erfordert `PICOVOICE_ACCESS_KEY`)
- **Manueller Modus**: Enter drücken zum Sprechen

---

## Architektur

```
Mikrofon → Whisper (lokal) → Claude API → ElevenLabs TTS → Lautsprecher
```

| Datei | Funktion |
|---|---|
| `main.py` | Hauptloop, State Machine, TTS-Streaming |
| `audio.py` | Mikrofon-Aufnahme, Wake Word, Thinking-Sound |
| `stt.py` | Whisper Speech-to-Text |
| `llm.py` | Claude API Streaming |
| `tts.py` | ElevenLabs TTS mit PCM-Streaming |
| `tools.py` | Tool-Definitionen und Executor |
| `context.py` | Dynamischer System Prompt (Notion, Kalender, BTC) |
| `brain.py` | Lokales Gedächtnis (brain/ JSON + git sync) |
| `notion_service.py` | Notion CRUD mit SQLite Cache |
| `calendar_service.py` | Google Calendar read/write |
| `email_service.py` | IMAP/SMTP mit VIP-Filter und Blacklist |
| `btc.py` | Bitcoin Live-Preis (CoinGecko) |
| `reminders_service.py` | Apple Reminders (Einkaufsliste, macOS only) |

---

## Gedächtnis (Brain)

JARVIS speichert Wissen in `brain/` — versioniert per Git, sync über alle Geräte.

| Datei | Inhalt |
|---|---|
| `brain/profile.json` | Simons Profil, Freelancing-Details |
| `brain/settings.json` | Aktive Routinen, Präferenzen, Email-VIP/Blacklist |
| `brain/memory.json` | Was JARVIS über Simon gelernt hat |

Per Sprache: *"Merk dir dass..."*, *"Von jetzt an..."*, *"Füge X zur Blacklist hinzu"*

---

## Tools

| Tool | Funktion |
|---|---|
| `notion_query/write/update/delete` | Notion Todos, Projekte, Konzepte |
| `calendar_query/write/delete` | Google Calendar |
| `email_query/send` | E-Mail lesen und senden |
| `brain_read/write` | Gedächtnis lesen/schreiben |
| `btc_price` | Bitcoin Live-Kurs |
| `shopping_add/get/remove` | Apple Reminders Einkaufsliste |
| `sync_email_vip` | VIP-Liste aus Notion Kontakte synchronisieren |

---

## Auf neuem Gerät

```bash
git clone https://github.com/justSimon13/j.a.r.v.i.s..git
# .env anlegen
pip install -r requirements.txt
python3 setup_google.py
python3 main.py
```

Brain ist automatisch aktuell via `git pull` beim Start.
