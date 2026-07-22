#!/bin/bash
# JARVIS Auto-Update — von jarvis-auto-update.timer periodisch aufgerufen.
#
# Prüft ob origin/main neue Commits hat, die hier noch fehlen. Wenn ja UND der
# Checkout gerade sauber ist (keine uncommitteten Änderungen — z.B. von Simon
# oder Claude Code über den SMB-Mount), pullt --ff-only und startet jarvis.service
# neu. Rührt den Checkout NICHT an wenn er gerade nicht sauber ist — lieber ein
# Durchlauf übersprungen als eine laufende Bearbeitung stören.
#
# Läuft als normaler User (nicht root), braucht dafür eine schmale sudoers-Regel
# nur für genau "systemctl restart jarvis" (siehe install_auto_update.sh).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

log() { echo "[auto_update] $(date '+%Y-%m-%dT%H:%M:%S%z') $*"; }

git fetch origin main --quiet

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "main" ]; then
    log "Checkout ist gerade nicht auf main (sondern $CURRENT_BRANCH) — überspringe."
    exit 0
fi

LOCAL_REV="$(git rev-parse HEAD)"
REMOTE_REV="$(git rev-parse origin/main)"

if [ "$LOCAL_REV" = "$REMOTE_REV" ]; then
    exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
    log "main hat neue Commits, aber Checkout ist gerade nicht sauber — überspringe."
    exit 0
fi

log "Neue Commits gefunden ($LOCAL_REV -> $REMOTE_REV), pulle..."
git pull --ff-only origin main

log "Starte jarvis.service neu..."
sudo -n /usr/bin/systemctl restart jarvis.service
log "Fertig, jetzt auf $(git rev-parse HEAD)."
