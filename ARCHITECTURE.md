# J.A.R.V.I.S. – Architektur

## Vision
Kein klassischer Assistent — ein aktives System das Simons Leben mitgestaltet.
Zentrale Logik auf einem lokalen Server, Clients sind reine Ein-/Ausgabe-Geräte.

---

## Repos

| Repo | Beschreibung |
|---|---|
| `jarvis` | Server — läuft 24/7 auf HP EliteDesk |
| `jarvis-app` | Mac + Windows Client mit GUI |
| `jarvis-satellite` | Headless Audio Client (Laptop, Raspberry Pi) |
| `jarvis-dashboard` | Wall Tablet PWA (React) — später |

---

## Hardware

| Gerät | Rolle |
|---|---|
| HP EliteDesk (Ubuntu Server) | Zentrales Gehirn, kein Audio direkt |
| MacBook | Client 1 — GUI + Audio |
| Alter Laptop (Linux) | Client 2 — Headless Audio (Schlafzimmer) |
| Raspberry Pi pro Raum | Client 3+ — Headless Audio (Multi-Room) |
| iPad / Android Tablet | Dashboard PWA — Transkript, Kalender, Todos |

Netzwerk: LAN/WLAN im Heimnetz. Remote-Zugriff via Tailscale.

---

## Server (`jarvis`)

### Dateistruktur

```
server.py           ← WebSocket Server, eine Pipeline-Instanz pro Client
pipeline.py         ← Herzstück: STT → LLM → TTS, callback-basiert
client_manager.py   ← Client-Registry, Active-Client-Tracking, Audio-Routing
protocol.py         ← WebSocket Message-Typen und PCM-Format-Konstanten
main.py             ← Standalone-Modus (Entwicklung/Fallback, keine WebSocket)
llm.py              ← LLM-Abstraktion (aktuell Claude, austauschbar)
stt.py              ← STT-Abstraktion (aktuell ElevenLabs Scribe, austauschbar)
tts.py              ← TTS-Abstraktion (aktuell ElevenLabs, austauschbar)
audio.py            ← Wake Word (OpenWakeWord) + VAD (Silero) — nur standalone
tools.py            ← Tool-Definitionen + execute()
context.py          ← System Prompt: build_static_prompt() + build_dynamic_prompt()
brain.py            ← Brain Storage (SQLite)
session_memory.py   ← Session History (SQLite)
config.py           ← Konfiguration aus .env
```

### pipeline.py — Interface

```python
class JarvisPipeline:
    def __init__(self, client_id: str, on_event, on_audio):
        # on_event(type, data)  → JSON-Event an Client senden
        # on_audio(pcm_bytes)   → PCM-Audio an Client senden

    def process_audio(self, wav_bytes: bytes)  # WAV → STT → process_text()
    def process_text(self, text: str)          # LLM streaming → TTS → Callbacks
```

Standalone (`main.py`):
```python
pipeline = JarvisPipeline(
    client_id="local",
    on_event=lambda t, d: ...,        # lokal verarbeiten
    on_audio=lambda pcm: sd.play(pcm)
)
```

Server (`server.py`):
```python
pipeline = JarvisPipeline(
    client_id=ws.id,
    on_event=lambda t, d: ws.send(json.dumps(...)),
    on_audio=lambda pcm: ws.send(pcm)
)
```

### client_manager.py — Interface

```python
class ClientManager:
    def register(self, client_id: str, send_audio: callable)
    def unregister(self, client_id: str)
    def set_active(self, client_id: str)
    def get_active(self) -> str
    def send_audio_to(self, client_id: str, pcm: bytes)
    def broadcast_event(self, type: str, data: dict)
```

### Datenbank (SQLite, lokal auf Server)

```
~/.jarvis/brain.db          ← Brain Storage (ersetzt Supabase)
~/.jarvis/sessions.db       ← Session History (ersetzt Supabase)
~/.jarvis/notion_cache.db   ← Notion API Cache (bereits SQLite, bleibt)
```

### Abstraktionsschichten

LLM und TTS/STT sind bewusst abstrahiert — Austausch möglich sobald lokale
Modelle gut genug sind (Ollama, Coqui, Piper).

---

## Client-Modi

| Client | Modus | Input | Output |
|---|---|---|---|
| `jarvis-app` | Voice-to-Voice | Mikrofon → WAV | PCM Audio abspielen |
| `jarvis-app` | Text-to-Text | Texteingabe | `response_chunk` anzeigen, kein Audio |
| `jarvis-satellite` | Voice-to-Voice | Mikrofon → WAV | PCM Audio abspielen |
| `jarvis-dashboard` | Text-to-Text | — | Transkript + Status anzeigen |

Text-to-Text signalisiert der Client mit `"tts": false` im `text_input` — Server überspringt TTS, spart Latenz und Kosten.

---

## Voice Pipeline

```
Mikrofon (Client)
    │
    ▼
Wake Word Detection     OpenWakeWord — hey_jarvis, ONNX
    │
    ▼
VAD Recording           Silero VAD — stoppt bei Redepause
    │  WAV bytes via WebSocket
    ▼
STT                     ElevenLabs Scribe  [abstrakt]
    │  Text
    ▼
LLM                     Claude claude-sonnet-4-6, streaming, Prompt Caching  [abstrakt]
    │  ├── Tool Call → execute() → Result → weiter streamen
    │  Text-Chunks + PCM via WebSocket
    ▼
TTS                     ElevenLabs, sentence-buffered  [abstrakt]  ← entfällt bei tts=false
    │  PCM (24kHz, mono, int16) via WebSocket
    ▼
Lautsprecher (Client)
```

---

## WebSocket Protokoll

Binary Frames:
- Client → Server: WAV-Audio (Spracheingabe)
- Server → Client: PCM-Audio (TTS-Antwort, 24kHz mono int16)

JSON Frames (Server → Client):
```
state            idle | listening | thinking | speaking | tool_running
status           Statustext ("Transkribiere…")
transcript       Erkannter Sprachtext
response_start   LLM-Antwort beginnt
response_chunk   Streaming-Chunk
response_done    Antwort abgeschlossen
tool             Tool wird ausgeführt
error            Fehlermeldung
```

JSON Frames (Client → Server):
```
text_input       {"type": "text_input", "text": "...", "tts": false}
ping / pong      Keep-alive
```

---

## Modi & Personas

Gleiche Wissensbasis, unterschiedliche Oberfläche.

| Modus | Ton | Ziel |
|---|---|---|
| Assistent | Präzise, reaktiv | Aufgaben erledigen |
| Coach | Fordernd, proaktiv | Wachstum einfordern |
| Fokus | Minimal | Unterbrechungen vermeiden |

Moduswechsel: manuell per Ansage, automatisch nach Tageszeit.
Jeder Modus hat eigene ElevenLabs-Stimme.

---

## Server-Setup (Schritt für Schritt)

### Schritt 1 — Daten migrieren (Mac)
```
python3 migrate.py
```
Zieht Brain + Sessions aus Supabase → SQLite. Gibt danach die scp-Befehle aus.

### Schritt 2 — HP EliteDesk einrichten
Ubuntu Server installieren (kein Desktop nötig), dann:
```
bash install_server.sh
```
Installiert Python-Umgebung, Abhängigkeiten und registriert einen systemd-Service
der JARVIS automatisch beim Boot startet.

### Schritt 3 — API Keys eintragen
```
nano ~/jarvis/.env
```
Folgende Keys eintragen: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `NOTION_API_KEY` etc.
`SUPABASE_URL` und `SUPABASE_KEY` weglassen — nicht mehr nötig.

### Schritt 4 — SQLite-Daten kopieren
```
scp ~/.jarvis/brain.db user@<server-ip>:~/.jarvis/brain.db
scp ~/.jarvis/sessions.db user@<server-ip>:~/.jarvis/sessions.db
```

### Schritt 5 — Server starten
```
sudo systemctl start jarvis
sudo systemctl status jarvis   # prüfen ob er läuft
journalctl -u jarvis -f        # Live-Logs
```

### Schritt 6 — Feste IP für den Server (Router)
Im Router-Interface (meistens 192.168.1.1 oder fritz.box) eine DHCP-Reservierung
für den HP EliteDesk einrichten — damit bekommt er immer dieselbe lokale IP.
Danach nie mehr ändern.

### Schritt 7 — Clients verbinden

**Mac:**
In `.env` des Mac-Repos:
```
JARVIS_SERVER=ws://192.168.1.xxx:8765
```
App neu starten → verbindet automatisch mit dem Server.

**Laptop (Headless):**
```
bash install_client.sh
nano ~/jarvis/.env   # JARVIS_SERVER=ws://192.168.1.xxx:8765 eintragen
systemctl --user start jarvis-client
```

---

## Remote-Zugriff (Tailscale) — später einrichten

Tailscale ist ein VPN-Tool das alle deine Geräte in ein virtuelles Netzwerk steckt —
egal ob zuhause oder unterwegs. Damit erreichst du den JARVIS-Server auch vom Café aus.

**Solange du nur zuhause bist: nicht nötig.** Lokale IP reicht völlig.

Einrichten wenn gewünscht:
1. Tailscale auf Server, Mac und Laptop installieren (`tailscale.com`)
2. Alle Geräte mit demselben Account einloggen
3. Tailscale-IP des Servers herausfinden: `tailscale ip`
4. `JARVIS_SERVER=ws://<tailscale-ip>:8765` in `.env` aller Clients setzen
5. Funktioniert dann überall — zuhause wie unterwegs, verschlüsselt, ohne offene Ports

---

## Offene Punkte

- [ ] HP EliteDesk: Ubuntu Server, systemd Service, feste IP
- [x] SQLite Schema für Brain + Session definieren
- [x] Migration Supabase → SQLite (`brain.py`, `session_memory.py`, `migrate.py`)
- [x] `pipeline.py` implementieren + `main.py` darauf umstellen
- [x] `client_manager.py` implementieren
- [x] `server.py` auf Pipeline + ClientManager umstellen
- [ ] `jarvis-app` Repo anlegen — GUI-Code aus `jarvis` rausziehen: `app.py`, `gui/`, `jarvis_engine.py`, `audio.py`, `protocol.py`, `config.py`, eigene `requirements.txt`
- [ ] `jarvis-satellite` Repo anlegen — Headless-Code rausziehen: `client.py`, `audio.py`, `protocol.py`, `config.py`, `requirements_client.txt` → wird zu `requirements.txt`
- [ ] Abstraktionsschicht LLM formalisieren
- [ ] Abstraktionsschicht TTS/STT formalisieren
- [ ] Background Task System (später)
- [ ] Tailscale auf Server + Clients einrichten
- [ ] Wall Tablet PWA (später)
