# J.A.R.V.I.S.

Persönlicher KI-Sprachassistent für macOS. Wake Word, Sprache, Chat-Fenster, Kalender, E-Mail, Notion, Timer, Wecker und mehr.

---

## Installation

### 1. Installer herunterladen

Aus dem [aktuellen Release](https://github.com/justSimon13/j.a.r.v.i.s./releases/latest) die Datei `JARVIS-installer.zip` herunterladen.

```bash
unzip JARVIS-installer.zip -d JARVIS
cd JARVIS
./install.sh
```

Das Skript kopiert alles nach `~/.jarvis/`, erstellt eine Python-Umgebung und legt `/Applications/JARVIS.app` an.

---

### 2. API Keys eintragen

```bash
nano ~/.jarvis/.env
```

| Variable | Woher | Pflicht |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Ja |
| `ELEVENLABS_API_KEY` | [elevenlabs.io](https://elevenlabs.io) | Ja |
| `ELEVENLABS_VOICE_ID` | ElevenLabs → Voices → Voice ID kopieren | Ja |
| `NOTION_API_KEY` | [notion.so/my-integrations](https://www.notion.so/my-integrations) → New Integration | Ja |
| `GITHUB_TOKEN` | GitHub → Settings → Developer Settings → Personal Access Tokens | Ja (für Brain-Sync) |
| `EMAIL_ADDRESS` | Deine E-Mail-Adresse | Nein |
| `WEATHER_CITY` | Stadtname für Wetter, z.B. `München` | Nein |
| `WHISPER_MODEL` | `tiny` / `base` / `small` / `medium` — schneller vs. genauer | Nein (Standard: `base`) |

---

### 3. Google Calendar einrichten (einmalig)

1. [Google Cloud Console](https://console.cloud.google.com) → Neues Projekt
2. APIs & Services → **Google Calendar API** aktivieren
3. APIs & Services → Anmeldedaten → **OAuth 2.0-Client-ID** erstellen (Typ: Desktop-App)
4. JSON herunterladen → speichern als `~/.jarvis/google_credentials.json`
5. Einmalig authentifizieren:

```bash
cd ~/.jarvis && .venv/bin/python3 setup_google.py
```

---

### 4. Apple Reminders freischalten (einmalig)

Systemeinstellungen → Datenschutz & Sicherheit → Automatisierung → Terminal (oder JARVIS.app) → **Erinnerungen** aktivieren

---

### 5. Notion-Integration verbinden

In jeder genutzten Notion-Datenbank (Todos, Projekte, Konzepte):  
Datenbank öffnen → `...` → **Verbindungen** → deine JARVIS-Integration hinzufügen

---

## Starten

```
/Applications/JARVIS.app
```

Oder per Spotlight: `cmd + space` → "JARVIS"

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
| Einkaufsliste | "Füge Milch und Brot zur Einkaufsliste hinzu" |
| Gedächtnis | "Merk dir dass ich Laktoseintolerant bin" |
| Wetter | "Wie wird das Wetter heute?" |
| Bitcoin | "Was ist der aktuelle BTC-Kurs?" |
| Websuche | "Suche nach den neuesten Nachrichten zu..." |
| Musik | "Spiel Lo-Fi" / "Nächster Song" / "Lauter" |
| Notion-Seiten | "Erstell eine Seite mit Checkliste für den Umzug" |

---

## Einstellungen

In der App oben rechts auf **⚙** klicken:
- Whisper-Modell wechseln
- Mikrofon auswählen
- Wake Word an/aus
- ElevenLabs Voice ID ändern
- API Keys aktualisieren
- Autostart beim Login aktivieren

---

## Architektur

```
Mikrofon → Silero VAD → Whisper (lokal) → Claude API → ElevenLabs TTS → Lautsprecher
                                                    ↕
                              Notion / Google Calendar / E-Mail / Brain
```

Brain (`~/.jarvis/brain/`) speichert dein Profil, Gedächtnis und Einstellungen — versioniert per Git und automatisch synchronisiert.

---

## Aktualisieren

Neues Release herunterladen → `install.sh` erneut ausführen. Deine `.env` und Brain-Daten bleiben erhalten.
