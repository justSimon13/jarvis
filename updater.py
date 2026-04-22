import subprocess
import tempfile
import threading
import urllib.request
import json
from pathlib import Path
import config


def _parse_version(v: str) -> tuple:
    return tuple(int(x) for x in v.strip().lstrip("v").split("."))


def check_latest() -> tuple[str, bool]:
    """Gibt (latest_version, is_newer) zurück. Wirft Exception bei Netzwerkfehler."""
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Updater"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    latest = data["tag_name"].lstrip("v")
    is_newer = _parse_version(latest) > _parse_version(config.VERSION)
    return latest, is_newer


def install_update(latest_version: str, on_progress, on_done, on_error):
    """Lädt ZIP herunter und öffnet install.sh im Terminal. Läuft in eigenem Thread."""
    def _run():
        try:
            on_progress("Lade Update herunter…")
            zip_url = f"https://github.com/{config.GITHUB_REPO}/releases/latest/download/JARVIS-installer.zip"
            tmp_dir = Path(tempfile.mkdtemp())
            zip_path = tmp_dir / "JARVIS-installer.zip"

            req = urllib.request.Request(zip_url, headers={"User-Agent": "JARVIS-Updater"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                zip_path.write_bytes(resp.read())

            on_progress("Entpacke…")
            extract_dir = tmp_dir / "JARVIS"
            extract_dir.mkdir()
            subprocess.run(["unzip", "-q", str(zip_path), "-d", str(extract_dir)], check=True)

            install_script = extract_dir / "install.sh"
            subprocess.run(["chmod", "+x", str(install_script)], check=True)

            on_progress("Öffne Terminal für Installation…")
            # Im Terminal ausführen damit User den Fortschritt sieht
            subprocess.run([
                "osascript", "-e",
                f'tell application "Terminal" to do script "bash \\"{install_script}\\" && echo \\"✓ Update abgeschlossen — JARVIS neu starten\\""'
            ])
            on_done(latest_version)

        except Exception as e:
            on_error(str(e))

    threading.Thread(target=_run, daemon=True).start()
