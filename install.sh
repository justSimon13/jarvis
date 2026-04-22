#!/bin/bash
# JARVIS Installer — kopiert alles nach ~/.jarvis und legt JARVIS.app in /Applications/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.jarvis"
APP_PATH="/Applications/JARVIS.app"
APP_NAME="JARVIS"

echo "Installiere J.A.R.V.I.S. nach $INSTALL_DIR ..."
echo ""

# ── 1. Dateien kopieren ────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
rsync -a \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='dist' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

# ── 2. Python-Version prüfen ───────────────────────────────────────────────────
PYTHON=$(which python3.14 2>/dev/null || which python3 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "✗ Python 3 nicht gefunden. Bitte installieren: brew install python@3.14"
    exit 1
fi
echo "✓ Python: $($PYTHON --version)"

# ── 3. Virtuelle Umgebung ──────────────────────────────────────────────────────
echo "Erstelle venv..."
cd "$INSTALL_DIR"
"$PYTHON" -m venv .venv
echo "✓ venv erstellt"

# ── 4. Dependencies installieren ──────────────────────────────────────────────
echo "Installiere Dependencies (kann einige Minuten dauern)..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "✓ Dependencies installiert"

# ── 5. .env anlegen ───────────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cat > "$INSTALL_DIR/.env" << 'ENV'
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
NOTION_API_KEY=
EMAIL_ADDRESS=
WEATHER_CITY=München
WHISPER_MODEL=base
AUDIO_INPUT_DEVICE=
MANUAL_MODE=false
ENV
    echo "⚠  API-Keys eintragen: nano $INSTALL_DIR/.env"
else
    echo "✓ .env bereits vorhanden"
fi

# ── 6. .app Bundle ────────────────────────────────────────────────────────────
echo "Erstelle JARVIS.app..."
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

cat > "$APP_PATH/Contents/MacOS/$APP_NAME" << LAUNCHER
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:\$DYLD_LIBRARY_PATH"
cd "$INSTALL_DIR"
exec "$INSTALL_DIR/.venv/bin/python3" "$INSTALL_DIR/app.py" "\$@"
LAUNCHER
chmod +x "$APP_PATH/Contents/MacOS/$APP_NAME"

if [ -f "$INSTALL_DIR/assets/icon.icns" ]; then
    cp "$INSTALL_DIR/assets/icon.icns" "$APP_PATH/Contents/Resources/icon.icns"
fi

cat > "$APP_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>JARVIS</string>
    <key>CFBundleDisplayName</key>
    <string>J.A.R.V.I.S.</string>
    <key>CFBundleIdentifier</key>
    <string>com.simonfischer.jarvis</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>JARVIS</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>J.A.R.V.I.S. benötigt Mikrofonzugriff für Spracherkennung.</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

xattr -cr "$APP_PATH"
echo "✓ /Applications/JARVIS.app erstellt"

# ── Fertig ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  J.A.R.V.I.S. erfolgreich installiert!"
echo "════════════════════════════════════════"
echo ""
echo "1. API-Keys eintragen:"
echo "   nano $INSTALL_DIR/.env"
echo ""
echo "2. JARVIS starten:"
echo "   open /Applications/JARVIS.app"
echo ""
