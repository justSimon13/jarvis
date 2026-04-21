#!/bin/bash
# Erstellt JARVIS-installer.zip — enthält alles außer .git, .venv, dist, .env

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/JARVIS-installer.zip"

cd "$SCRIPT_DIR"

rm -f "$OUT"

zip -r "$OUT" . \
    --exclude "*.git*" \
    --exclude ".venv/*" \
    --exclude "dist/*" \
    --exclude "__pycache__/*" \
    --exclude "*.pyc" \
    --exclude ".env" \
    --exclude "JARVIS-installer.zip"

echo "✓ $OUT erstellt ($(du -sh "$OUT" | cut -f1))"
echo ""
echo "Weitergeben per AirDrop, dann auf dem Ziel-Mac:"
echo "  unzip JARVIS-installer.zip -d JARVIS"
echo "  cd JARVIS && ./install.sh"
