#!/bin/bash
# J.A.R.V.I.S. Client Installer — Linux (Headless Audio Client)
# Voraussetzung: git, python3.11+, pip
# Ausführen als normaler User mit sudo-Rechten.

set -e

REPO="https://github.com/justSimon13/jarvis.git"
INSTALL_DIR="$HOME/jarvis"
SERVICE_NAME="jarvis-client"
PYTHON=$(command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || command -v python3 2>/dev/null)

echo ""
echo "════════════════════════════════════════"
echo "  J.A.R.V.I.S. Client Installer"
echo "════════════════════════════════════════"
echo ""

# ── 1. System-Pakete ──────────────────────────────────────────────────────────
echo "Installiere System-Abhängigkeiten..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git python3 python3-pip python3-venv \
    portaudio19-dev ffmpeg \
    libsndfile1
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
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements_client.txt"
echo "✓ Python-Umgebung"

# ── 4. .env anlegen (wenn nicht vorhanden) ────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cat > "$INSTALL_DIR/.env" << 'ENV'
JARVIS_SERVER=ws://100.x.x.x:8765
MANUAL_MODE=false
AUDIO_INPUT_DEVICE=
ENV
    echo "✓ .env erstellt — Server-IP bitte eintragen: nano $INSTALL_DIR/.env"
else
    echo "✓ .env bereits vorhanden"
fi

# ── 5. systemd User-Service ───────────────────────────────────────────────────
mkdir -p "$HOME/.config/systemd/user"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=J.A.R.V.I.S. Audio Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python3 $INSTALL_DIR/client.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
echo "✓ systemd User-Service registriert"

# ── Fertig ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  Installation abgeschlossen!"
echo "════════════════════════════════════════"
echo ""
echo "  Nächste Schritte:"
echo "  1. Server-IP eintragen:   nano $INSTALL_DIR/.env"
echo "  2. Client starten:        systemctl --user start $SERVICE_NAME"
echo "  3. Logs verfolgen:        journalctl --user -u $SERVICE_NAME -f"
echo ""
echo "  Oder direkt testen (ohne Service):"
echo "  cd $INSTALL_DIR && .venv/bin/python3 client.py"
echo ""
