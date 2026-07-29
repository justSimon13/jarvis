# J.A.R.V.I.S.

Persönlicher KI-Sprachassistent für macOS. Wake Word, Sprache, Chat-Fenster, Kalender, E-Mail, Todos/Projekte, Timer, Wecker und mehr.

---

## Installation

### 1. Installer herunterladen & ausführen

Aus dem [aktuellen Release](https://github.com/justSimon13/jarvis/releases/latest) die Datei `JARVIS-installer.zip` herunterladen.

```bash
unzip JARVIS-installer.zip -d JARVIS
cd JARVIS
./install.sh
```

Das Skript installiert automatisch alle Abhängigkeiten (Homebrew, Python, PortAudio, ffmpeg), kopiert alles nach `~/.jarvis/` und legt `/Applications/JARVIS.app` an.

---

### 2. JARVIS starten & einrichten

```
open /Applications/JARVIS.app
```

Beim ersten Start öffnet sich automatisch der **Setup Wizard** — API Keys, Google Calendar und E-Mail direkt in der App eingeben. Jeder Schritt kann übersprungen und später in den Einstellungen nachgetragen werden.

---

### 3. API Keys

| Key | Woher | Pflicht |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Ja |
| `ELEVENLABS_API_KEY` | [elevenlabs.io](https://elevenlabs.io) | Ja |
| `ELEVENLABS_VOICE_ID` | ElevenLabs → Voices → Voice ID kopieren | Ja |
| `EMAIL_ADDRESS` | Deine E-Mail-Adresse | Nein |
| `WEATHER_CITY` | Stadtname für Wetter, z.B. `München` | Nein |

---

### 4. Google Calendar einrichten (optional)

Im Setup Wizard oder in den Einstellungen (⚙):

1. [Google Cloud Console](https://console.cloud.google.com) → Neues Projekt
2. APIs & Services → **Google Calendar API** aktivieren
3. APIs & Services → Anmeldedaten → **OAuth 2.0-Client-ID** erstellen (Typ: Desktop-App)
4. JSON herunterladen
5. In JARVIS: ⚙ → Google Calendar → **credentials.json auswählen** → **Verbinden**

---

### 5. Apple Reminders freischalten (für Wecker)

Systemeinstellungen → Datenschutz & Sicherheit → Automatisierung → JARVIS.app → **Erinnerungen** aktivieren

---

## Bedienung

| Modus | Aktivierung | Eingabe | Ausgabe |
|---|---|---|---|
| **Voice** | "Hey JARVIS" sagen | Sprache | Sprache + Chat |
| **Text** | Button oben rechts → "Text" | Tippen + Enter | Chat |

Mitten im Satz pausieren ist okay — JARVIS wartet bis du fertig bist (bis zu 10 Sekunden).

---

## Was JARVIS kann

| Funktion | Beispiel |
|---|---|
| Todos & Projekte | "Erstell ein Todo: Zahnarzt anrufen, Priorität hoch" |
| Google Calendar | "Was steht diese Woche an?" / "Trag Montag 10 Uhr Meeting ein" |
| E-Mail | "Habe ich neue wichtige Mails?" / "Schreib eine Mail an Max" |
| Timer | "Stell einen Timer auf 10 Minuten, Nudeln" |
| Wecker | "Wecker um 7:30 Uhr, Aufstehen" (synct via iCloud aufs iPhone) |
| Gedächtnis | "Merk dir dass ich Laktoseintolerant bin" |
| Wetter | "Wie wird das Wetter heute?" |
| Bitcoin | "Was ist der aktuelle BTC-Kurs?" |
| Websuche | "Suche nach den neuesten Nachrichten zu..." |
| Musik | "Spiel Lo-Fi" / "Nächster Song" / "Lauter" |
| Todos/Projekte | "Leg ein Todo an: Umzug organisieren" |

---

## Einstellungen

In der App oben rechts auf **⚙** klicken:
- Whisper-Modell wechseln
- Mikrofon auswählen
- Wake Word an/aus
- ElevenLabs Voice ID ändern
- Google Calendar verbinden
- API Keys aktualisieren
- Autostart beim Login aktivieren

---

## Architektur

```
Mikrofon → Silero VAD → Whisper (lokal) → Claude API → ElevenLabs TTS → Lautsprecher
                                                    ↕
                         Todos/Projekte (lokal) / Google Calendar / E-Mail / Brain
```

Brain (Profil, Gedächtnis, Einstellungen) wird in Supabase gespeichert — geräteübergreifend synchronisiert, kein GitHub nötig.

---

## Entwicklung starten

Für lokale Entwicklung reicht ein Python-Setup ohne die gebaute App:

```bash
git clone https://github.com/justSimon13/jarvis.git
cd jarvis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`.env` im Projektverzeichnis anlegen (mind. `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` — Rest optional, siehe [API Keys](#3-api-keys)):

```
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
EMAIL_ADDRESS=
WEATHER_CITY=Stuttgart
JARVIS_HOST=0.0.0.0
JARVIS_PORT=8765
```

Zwei Start-Varianten:

```bash
# Standalone (Terminal, kein Server nötig — Mikrofon/Lautsprecher direkt am eigenen Rechner)
python3 main.py

# WebSocket-Server (für Clients wie jarvis-web / Satellite), Standard-Port 8765
python3 server.py
```

`JARVIS_HOST`/`JARVIS_PORT` überschreiben Host/Port des Servers bei Bedarf.

---

## Aktualisieren

Neues Release herunterladen → `install.sh` erneut ausführen. Deine `.env` und Brain-Daten bleiben erhalten.
