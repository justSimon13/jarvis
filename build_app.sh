#!/bin/bash
# Erstellt JARVIS.app als natives macOS App-Bundle (ohne py2app)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="JARVIS"
APP_PATH="$SCRIPT_DIR/dist/$APP_NAME.app"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

echo "Baue $APP_NAME.app..."

# Verzeichnisstruktur
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# Launcher-Skript
cat > "$APP_PATH/Contents/MacOS/$APP_NAME" << EOF
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:\$DYLD_LIBRARY_PATH"
APP_DIR="$SCRIPT_DIR"
cd "\$APP_DIR"
exec "\$APP_DIR/.venv/bin/python3" "\$APP_DIR/app.py" "\$@"
EOF
chmod +x "$APP_PATH/Contents/MacOS/$APP_NAME"

# Icon kopieren
if [ -f "$SCRIPT_DIR/assets/icon.icns" ]; then
    cp "$SCRIPT_DIR/assets/icon.icns" "$APP_PATH/Contents/Resources/icon.icns"
fi

# Info.plist
cat > "$APP_PATH/Contents/Info.plist" << EOF
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
    <string>$APP_NAME</string>
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
EOF

echo "✓ Erstellt: $APP_PATH"
echo ""
echo "Zum Starten:"
echo "  open \"$APP_PATH\""
echo ""
echo "Zum Installieren (Applications-Ordner):"
echo "  cp -r \"$APP_PATH\" /Applications/"
