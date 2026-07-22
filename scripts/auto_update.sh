#!/bin/bash
# JARVIS Auto-Update — von jarvis-auto-update.timer periodisch aufgerufen.
#
# Prüft ob origin/main neue Commits hat, die hier noch fehlen. Wenn ja UND der
# Checkout gerade sauber ist (keine uncommitteten Änderungen — z.B. von Simon
# oder Claude Code über den SMB-Mount), pullt --ff-only sofort (rührt nur den
# Checkout an, stört nichts Laufendes). Der Neustart von jarvis.service selbst
# wird aber verschoben bis JARVIS gerade nicht aktiv genutzt wird (keine
# verbundenen Clients, kein laufender Coding-Task) — sonst würde ein Neustart
# mitten in einem Gespräch die Verbindung killen (2026-07-22: "dann soll er
# halt restarten, wenn alle Prozesse abgeschlossen sind und er für seinen Teil
# fertig ist"). Ein ausstehender Neustart wird per Marker-Datei über mehrere
# Timer-Durchläufe gemerkt, bis JARVIS wieder idle ist.
#
# Läuft als normaler User (nicht root), braucht dafür eine schmale sudoers-Regel
# nur für genau "systemctl restart jarvis" (siehe install_auto_update.sh).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

JARVIS_DIR="$HOME/.jarvis"
RESTART_MARKER="$JARVIS_DIR/jarvis_restart_pending"
IDLE_STATUS_FILE="$JARVIS_DIR/idle_status.json"

log() { echo "[auto_update] $(date '+%Y-%m-%dT%H:%M:%S%z') $*"; }

git fetch origin main --quiet

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "main" ]; then
    log "Checkout ist gerade nicht auf main (sondern $CURRENT_BRANCH) — überspringe."
    exit 0
fi

LOCAL_REV="$(git rev-parse HEAD)"
REMOTE_REV="$(git rev-parse origin/main)"

if [ "$LOCAL_REV" != "$REMOTE_REV" ]; then
    if [ -n "$(git status --porcelain)" ]; then
        log "main hat neue Commits, aber Checkout ist gerade nicht sauber — überspringe."
        exit 0
    fi
    log "Neue Commits gefunden ($LOCAL_REV -> $REMOTE_REV), pulle..."
    git pull --ff-only origin main
    mkdir -p "$JARVIS_DIR"
    touch "$RESTART_MARKER"
fi

if [ ! -f "$RESTART_MARKER" ]; then
    exit 0  # nichts Neues und kein ausstehender Neustart
fi

# jarvis.service läuft gerade gar nicht (z.B. abgestürzt)? Dann gibt es nichts
# zu unterbrechen — sofort neu starten statt auf "idle" zu warten.
if systemctl is-active --quiet jarvis.service; then
    BUSY="0"
    if [ -f "$IDLE_STATUS_FILE" ]; then
        BUSY=$(python3 -c "
import json
try:
    with open('$IDLE_STATUS_FILE') as f:
        s = json.load(f)
    print('1' if (s.get('connected_clients', 0) > 0 or s.get('coding_task_active')) else '0')
except Exception:
    print('0')
")
    fi
    if [ "$BUSY" = "1" ]; then
        log "Neustart steht aus, aber JARVIS ist gerade aktiv (Verbindung/Coding-Task) — verschiebe auf nächsten Durchlauf."
        exit 0
    fi
fi

log "Starte jarvis.service neu..."
sudo -n /usr/bin/systemctl restart jarvis.service
rm -f "$RESTART_MARKER"
log "Fertig, jetzt auf $(git rev-parse HEAD)."
