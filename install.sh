#!/bin/bash
# JARVIS Installer — vollständige Installation inkl. Abhängigkeiten

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.jarvis"
APP_PATH="/Applications/JARVIS.app"
APP_NAME="JARVIS"

echo ""
echo "════════════════════════════════════════"
echo "  J.A.R.V.I.S. Installer"
echo "════════════════════════════════════════"
echo ""

# ── 1. Homebrew ────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "Installiere Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # PATH für den Rest des Skripts setzen
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    echo "✓ Homebrew installiert"
else
    echo "✓ Homebrew vorhanden"
fi

# ── 2. Python ──────────────────────────────────────────────────────────────────
PYTHON=$(command -v python3.14 2>/dev/null || command -v python3.13 2>/dev/null || command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || true)
if [ -z "$PYTHON" ]; then
    echo "Installiere Python..."
    brew install python@3.13
    PYTHON="$(brew --prefix)/bin/python3.13"
    echo "✓ Python installiert"
else
    echo "✓ Python: $($PYTHON --version)"
fi

# ── 3. System-Abhängigkeiten ───────────────────────────────────────────────────
echo "Prüfe System-Abhängigkeiten..."

if ! brew list portaudio &>/dev/null; then
    echo "  Installiere portaudio..."
    brew install portaudio
fi
echo "✓ portaudio"

if ! brew list ffmpeg &>/dev/null; then
    echo "  Installiere ffmpeg (für Whisper)..."
    brew install ffmpeg
fi
echo "✓ ffmpeg"

# ── 4. Dateien kopieren ────────────────────────────────────────────────────────
echo "Kopiere Dateien nach $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rsync -a \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='dist' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"
echo "✓ Dateien kopiert"

# ── 5. Virtuelle Umgebung ──────────────────────────────────────────────────────
echo "Erstelle Python-Umgebung..."
cd "$INSTALL_DIR"
"$PYTHON" -m venv .venv
echo "✓ venv erstellt"

# ── 6. Dependencies installieren ──────────────────────────────────────────────
echo "Installiere Python-Pakete (kann einige Minuten dauern)..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "✓ Pakete installiert"

# ── 7. .env anlegen ───────────────────────────────────────────────────────────
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
fi

# ── 8. .app Bundle ────────────────────────────────────────────────────────────
echo "Erstelle JARVIS.app..."
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

cat > "$APP_PATH/Contents/MacOS/$APP_NAME" << LAUNCHER
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
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
    <string>1.2.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2</string>
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
echo "  open /Applications/JARVIS.app"
echo ""
echo "  Beim ersten Start öffnet sich der Setup Wizard."
echo ""
