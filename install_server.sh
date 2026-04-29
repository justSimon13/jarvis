#!/bin/bash
# J.A.R.V.I.S. Server Installer — Linux (HP EliteDesk, Ubuntu/Debian)
# Voraussetzung: git, python3.11+, pip
# Ausführen als normaler User mit sudo-Rechten.

set -e

REPO="https://github.com/justSimon13/jarvis.git"
INSTALL_DIR="$HOME/jarvis"
SERVICE_NAME="jarvis"
PYTHON=$(command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || command -v python3 2>/dev/null)

echo ""
echo "════════════════════════════════════════"
echo "  J.A.R.V.I.S. Server Installer"
echo "════════════════════════════════════════"
echo ""

# ── 1. System-Pakete ──────────────────────────────────────────────────────────
echo "Installiere System-Abhängigkeiten..."
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-pip python3-venv ffmpeg
echo "✓ System-Pakete"

# ── 2. Repo klonen oder aktualisieren ─────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Aktualisiere bestehendes Repo..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "Klone Repo nach $INSTALL_DIR ..."
    git clone "$REPO" "$INSTALL_DIR"
fi
echo "✓ Code"

# ── 3. Virtuelle Umgebung ─────────────────────────────────────────────────────
echo "Erstelle Python-Umgebung..."
"$PYTHON" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "✓ Python-Umgebung"

# ── 4. .env anlegen (wenn nicht vorhanden) ────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cat > "$INSTALL_DIR/.env" << 'ENV'
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
NOTION_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
EMAIL_ADDRESS=
EMAIL_IMAP_HOST=
EMAIL_SMTP_HOST=
EMAIL_PASSWORD=
EMAIL_SEND_ENABLED=false
WEATHER_CITY=Stuttgart
JARVIS_HOST=0.0.0.0
JARVIS_PORT=8765
ENV
    echo "✓ .env erstellt — API-Keys bitte eintragen: nano $INSTALL_DIR/.env"
else
    echo "✓ .env bereits vorhanden"
fi

# ── 5. systemd Service ────────────────────────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=J.A.R.V.I.S. WebSocket Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python3 $INSTALL_DIR/server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "✓ systemd Service registriert"

# ── Fertig ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  Installation abgeschlossen!"
echo "════════════════════════════════════════"
echo ""
echo "  Nächste Schritte:"
echo "  1. API-Keys eintragen:  nano $INSTALL_DIR/.env"
echo "  2. Server starten:      sudo systemctl start $SERVICE_NAME"
echo "  3. Logs verfolgen:      journalctl -u $SERVICE_NAME -f"
echo ""
echo "  Google Calendar Auth (einmalig, am Mac ausführen):"
echo "  python3 setup_google.py"
echo "  → token kopieren nach: $INSTALL_DIR/google_token.json"
echo ""
