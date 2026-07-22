#!/bin/bash
# J.A.R.V.I.S. Auto-Update Installer — Linux (HP EliteDesk)
# Richtet einen systemd-Timer ein, der alle 5 Minuten prüft ob origin/main neue
# Commits hat (z.B. weil ein von JARVIS' Coding-Engine erstellter PR gemergt
# wurde) und JARVIS dann automatisch pullt + neu startet. Kein manuelles SSH
# mehr nötig für "neuer Code ist da, JARVIS soll ihn jetzt auch nutzen".
#
# Einmalig ausführen als der User, der auch jarvis.service betreibt:
#   bash install_auto_update.sh
set -e

INSTALL_DIR="$HOME/jarvis"
SERVICE_NAME="jarvis-auto-update"

echo ""
echo "════════════════════════════════════════"
echo "  J.A.R.V.I.S. Auto-Update Installer"
echo "════════════════════════════════════════"
echo ""

# ── 1. Schmale sudoers-Regel: nur "systemctl restart jarvis", ohne Passwort ───
# Kein Passwort nötig, weil der Timer unbeaufsichtigt läuft (kein TTY für eine
# Passwort-Abfrage). Bewusst NICHT voller sudo-Zugriff — genau ein Befehl.
# Pfad hart auf /usr/bin/systemctl statt "$(command -v systemctl)" — muss exakt
# mit dem Pfad übereinstimmen, den scripts/auto_update.sh aufruft, sonst greift
# die Regel nicht (sudoers braucht einen exakten, absoluten Pfad-Match).
SUDOERS_FILE="/etc/sudoers.d/jarvis-auto-update"
if [ ! -x /usr/bin/systemctl ]; then
    echo "✗ /usr/bin/systemctl nicht gefunden — Pfad in install_auto_update.sh und scripts/auto_update.sh anpassen."
    exit 1
fi
echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart jarvis.service" | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" > /dev/null
echo "✓ sudoers-Regel ($SUDOERS_FILE)"

# ── 2. systemd Service (oneshot, führt scripts/auto_update.sh aus) ────────────
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << EOF
[Unit]
Description=JARVIS Auto-Update (pull + restart bei neuen Commits auf main)
After=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/bin/bash $INSTALL_DIR/scripts/auto_update.sh
EOF
echo "✓ ${SERVICE_NAME}.service"

# ── 3. systemd Timer (alle 5 Minuten) ─────────────────────────────────────────
sudo tee "/etc/systemd/system/${SERVICE_NAME}.timer" > /dev/null << EOF
[Unit]
Description=Prüft alle 5 Minuten auf neue JARVIS-Commits (Auto-Update)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF
echo "✓ ${SERVICE_NAME}.timer"

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.timer"
echo "✓ Timer aktiviert"

echo ""
echo "════════════════════════════════════════"
echo "  Fertig!"
echo "════════════════════════════════════════"
echo ""
echo "  Status prüfen:   systemctl list-timers ${SERVICE_NAME}.timer"
echo "  Log verfolgen:    journalctl -u ${SERVICE_NAME}.service -f"
echo "  Manuell auslösen: sudo systemctl start ${SERVICE_NAME}.service"
echo ""
