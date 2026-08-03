#!/usr/bin/env bash
# Installiert den täglichen Backup-Timer. Auf dem Server ausführen:
#     bash scripts/install_backup.sh
#
# Idempotent: erneutes Ausführen ersetzt die Units und lädt systemd neu.
# Platzhalter-Ersetzung nach demselben Muster wie install_server.sh.
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"

echo "[install_backup] Verzeichnis: $INSTALL_DIR"
echo "[install_backup] Benutzer:    $RUN_USER"

# Erst ein echter Probelauf — ein Timer, der beim ersten Feuern scheitert, fällt
# sonst monatelang nicht auf.
echo "[install_backup] Probelauf..."
/usr/bin/python3 "$INSTALL_DIR/scripts/backup.py" --tag installation

sed -e "s|__USER__|$RUN_USER|g" -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    "$INSTALL_DIR/scripts/jarvis-backup.service" | sudo tee /etc/systemd/system/jarvis-backup.service >/dev/null
sudo cp "$INSTALL_DIR/scripts/jarvis-backup.timer" /etc/systemd/system/jarvis-backup.timer

sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-backup.timer

echo
echo "[install_backup] Fertig. Prüfen mit:"
echo "    systemctl list-timers jarvis-backup"
echo "    journalctl -u jarvis-backup -n 30"
echo "    python3 $INSTALL_DIR/scripts/backup.py --list"
