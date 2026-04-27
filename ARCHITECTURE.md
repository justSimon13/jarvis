# J.A.R.V.I.S. – Architektur

## Vision
Kein klassischer Assistent — ein aktives System das Simons Leben mitgestaltet.
Zentrale Logik auf einem lokalen Server, Clients sind reine Ein-/Ausgabe-Geräte.

---

## Repos

| Repo | URL | Beschreibung |
|---|---|---|
| `jarvis` | github.com/justSimon13/jarvis | Server — läuft 24/7 auf HP EliteDesk |
| `jarvis-app` | github.com/justSimon13/jarvis-app | Mac + Windows Client mit GUI |
| `jarvis-satellite` | github.com/justSimon13/jarvis-satellite | Headless Audio Client (Laptop, Raspberry Pi) |
| `jarvis-dashboard` | — | Wall Tablet PWA (React) — später |

---

## Hardware

| Gerät | Rolle | Status |
|---|---|---|
| HP EliteDesk (Ubuntu Server) | Zentrales Gehirn, 24/7 | ⏳ Netzteil fehlt noch |
| MacBook | Client 1 — GUI + Audio | ✅ läuft (standalone / server) |
| Alter Laptop (Ubuntu) | Client 2 — Headless Audio | 🔧 einrichten |
| Raspberry Pi pro Raum | Client 3+ — Headless Audio | später |
| iPad / Android Tablet | Dashboard PWA | später |

Netzwerk: LAN/WLAN im Heimnetz. Remote-Zugriff via Tailscale (später).

---

## Wie alles zusammenhängt

```
┌─────────────────────────────────────────────────────┐
│                   JARVIS Server                     │
│  server.py  →  pipeline.py  →  llm / tts / stt     │
│  Läuft auf: Mac (jetzt) → HP EliteDesk (später)     │
└──────────────┬──────────────────────────────────────┘
               │ WebSocket (ws://192.168.x.x:8765)
       ┌───────┴────────┐
       │                │
  jarvis-app       jarvis-satellite
  (Mac GUI)        (Ubuntu Laptop, headless)
  Mikrofon → WAV   Mikrofon → WAV
  PCM → Speaker    PCM → Speaker
```

**Wichtig:** Clients haben keine Logik, keine API Keys (außer JARVIS_SERVER).
Alles — Claude, ElevenLabs, Notion, Brain — läuft auf dem Server.

---

## Modi

| Modus | Beschreibung |
|---|---|
| **Standalone** | `python3 main.py` auf dem Mac. Kein Server nötig, alles lokal. Für Entwicklung. |
| **Server** | `python3 server.py` — startet WebSocket Server, Clients verbinden sich. |
| **Client** | `python3 client.py` (satellite) oder `python3 app.py` (GUI). Braucht laufenden Server. |

---

## Server-Dateien (`jarvis`)

```
server.py           ← WebSocket Server, eine Pipeline pro Client
pipeline.py         ← STT → LLM → TTS, callback-basiert
client_manager.py   ← Client-Registry, Audio-Routing
protocol.py         ← Nachrichten-Typen + PCM-Format
main.py             ← Standalone-Modus (kein WebSocket, Entwicklung)
llm.py              ← Claude (austauschbar gegen Ollama etc.)
stt.py              ← ElevenLabs Scribe (austauschbar)
tts.py              ← ElevenLabs TTS (austauschbar)
audio.py            ← Wake Word + VAD — nur im Standalone-Modus
tools.py            ← Tool-Definitionen + execute()
context.py          ← System Prompt Builder
brain.py            ← Langzeit-Speicher (SQLite)
session_memory.py   ← Session History (SQLite)
config.py           ← Konfiguration aus .env
```

### Datenbanken (lokal auf Server)
```
~/.jarvis/brain.db          ← Profil, Einstellungen, Memory, Follow-ups
~/.jarvis/sessions.db       ← Session History (Zusammenfassungen)
~/.jarvis/notion_cache.db   ← Notion API Cache
```

---

## Voice Pipeline

```
Mikrofon (Client)
    │
    ▼  lokal auf Client
Wake Word Detection     OpenWakeWord — hey_jarvis
    │
    ▼  lokal auf Client
VAD Recording           Silero VAD — stoppt bei Redepause
    │  WAV via WebSocket → Server
    ▼  auf Server
STT                     ElevenLabs Scribe
    │
    ▼  auf Server
LLM                     Claude claude-sonnet-4-6, streaming
    │  ├── Tool Call → execute() → weiter
    │  PCM via WebSocket → Client
    ▼  auf Server
TTS                     ElevenLabs (entfällt bei tts=false)
    │
    ▼  auf Client
Lautsprecher
```

---

## WebSocket Protokoll

**Binary:** Client → Server: WAV | Server → Client: PCM (24kHz, mono, int16)

**JSON Server → Client:**
```
state            idle | listening | thinking | speaking | tool_running
transcript       Erkannter Text
response_chunk   Streaming-Chunk
response_done    Antwort fertig
tool             Tool-Name
error            Fehlermeldung
```

**JSON Client → Server:**
```
text_input    {"type": "text_input", "text": "...", "tts": true/false}
              tts=false → kein Audio, nur Text (Text-Mode)
```

---

## Setup-Guide

### Situation A — Standalone (Mac, kein Server)
Für Entwicklung und solange kein Server läuft.
```bash
cd jarvis
python3 main.py
```
Fertig. Alles läuft lokal auf dem Mac.

---

### Situation B — Mac als Server + Ubuntu Laptop als Client (aktuell)

#### Schritt 1: Mac-IP herausfinden
```bash
# auf dem Mac:
ipconfig getifaddr en0        # WLAN
ipconfig getifaddr en1        # LAN (falls per Kabel)
# Ergebnis z.B. 192.168.1.42
```

#### Schritt 2: Server auf dem Mac starten
```bash
cd /path/to/jarvis
python3 server.py
# → [server] J.A.R.V.I.S. bereit — ws://0.0.0.0:8765
```
Der Server läuft jetzt und wartet auf Clients.

#### Schritt 3: Ubuntu Laptop einrichten
```bash
# System-Abhängigkeiten
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git portaudio19-dev

# Repo klonen
git clone https://github.com/justSimon13/jarvis-satellite
cd jarvis-satellite

# Python-Umgebung
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Konfiguration
cp .env.example .env
nano .env
```

In der `.env` eintragen:
```
JARVIS_SERVER=ws://192.168.1.42:8765   ← Mac-IP von Schritt 1
MANUAL_MODE=false
AUDIO_INPUT_DEVICE=                     ← leer lassen, auto-detect
```

#### Schritt 4: Client starten
```bash
source .venv/bin/activate
python3 client.py
# → [client] Verbinde mit ws://192.168.1.42:8765…
# → [client] Verbunden!
# → [client] Warte auf Wake Word…
```
Sag "Hey JARVIS" — der Mac verarbeitet, der Laptop spricht.

---

### Situation C — HP EliteDesk als Server (sobald Netzteil da)

#### Schritt 1: Ubuntu Server installieren
Minimal-Installation, kein Desktop.

#### Schritt 2: Server einrichten
```bash
git clone https://github.com/justSimon13/jarvis
cd jarvis
bash install_server.sh
```

#### Schritt 3: API Keys eintragen
```bash
nano .env
```
Eintragen: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`,
`NOTION_API_KEY`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD` etc.
`SUPABASE_URL` und `SUPABASE_KEY` weglassen — nicht mehr nötig.

#### Schritt 4: Brain-Daten kopieren (vom Mac)
```bash
# auf dem Mac:
SERVER_IP=192.168.1.xxx   # IP des HP EliteDesk
scp ~/.jarvis/brain.db simon@$SERVER_IP:~/.jarvis/brain.db
scp ~/.jarvis/sessions.db simon@$SERVER_IP:~/.jarvis/sessions.db
```

#### Schritt 5: Server starten
```bash
sudo systemctl start jarvis
sudo systemctl status jarvis
journalctl -u jarvis -f        # Live-Logs
```

#### Schritt 6: Feste IP im Router
Im Router-Interface (meistens fritz.box oder 192.168.1.1):
DHCP-Reservierung für den HP EliteDesk einrichten.
Damit bekommt er immer dieselbe IP — einmal konfigurieren, nie wieder ändern.

#### Schritt 7: Clients auf neue Server-IP umstellen
In `.env` jedes Clients:
```
JARVIS_SERVER=ws://192.168.1.xxx:8765   ← IP des HP EliteDesk
```

---

## Remote-Zugriff (Tailscale) — später

Tailscale steckt alle Geräte in ein privates VPN — erreichbar von überall.
**Solange du nur zuhause bist: nicht nötig.**

Einrichten wenn gewünscht:
1. `tailscale.com` → auf Server, Mac und Laptop installieren
2. Alle mit demselben Account einloggen
3. `tailscale ip` auf dem Server → gibt die Tailscale-IP zurück
4. `JARVIS_SERVER=ws://<tailscale-ip>:8765` in alle Clients eintragen

---

## Offene Punkte

- [x] SQLite Brain + Sessions (brain.py, session_memory.py)
- [x] Migration Supabase → SQLite (migrate.py)
- [x] pipeline.py + client_manager.py implementieren
- [x] server.py auf Pipeline umgestellt
- [x] jarvis-app Repo angelegt (github.com/justSimon13/jarvis-app)
- [x] jarvis-satellite Repo angelegt (github.com/justSimon13/jarvis-satellite)
- [ ] HP EliteDesk: Ubuntu Server, systemd Service, feste IP
- [ ] Ubuntu Laptop als Client 2 einrichten (jarvis-satellite)
- [ ] Tailscale auf Server + Clients einrichten
- [ ] Abstraktionsschicht LLM (Claude → Ollama)
- [ ] Abstraktionsschicht TTS/STT
- [ ] Background Task System
- [ ] Wall Tablet PWA (jarvis-dashboard)
