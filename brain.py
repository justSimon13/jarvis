import json
import subprocess
from pathlib import Path

_BRAIN_DIR = Path(__file__).parent / "brain"
_BRAIN_DIR.mkdir(exist_ok=True)

_SECTIONS = ["profile", "settings", "memory"]


def _path(section: str) -> Path:
    return _BRAIN_DIR / f"{section}.json"


def _read(section: str) -> dict:
    p = _path(section)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(section: str, data: dict):
    _path(section).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _git_push(message: str):
    cwd = Path(__file__).parent
    try:
        subprocess.run(["git", "add", "brain/"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=cwd, capture_output=True)
    except subprocess.CalledProcessError:
        pass  # nichts zu committen oder kein remote – kein Fehler


def sync():
    """Aktuellen Stand vom Remote holen (beim App-Start)."""
    cwd = Path(__file__).parent
    try:
        subprocess.run(["git", "pull", "--ff-only"], cwd=cwd, capture_output=True)
    except Exception:
        pass


def load() -> dict:
    return {s: _read(s) for s in _SECTIONS}


def read(section: str, key: str | None = None):
    data = _read(section)
    return data.get(key) if key else data


def write(section: str, key: str, value) -> str:
    data = _read(section)
    data[key] = value
    _write(section, data)
    _git_push(f"JARVIS: {section}.{key} aktualisiert")
    return f"Gespeichert: {section} → {key} = {value}"


def build_prompt_section() -> str:
    data = load()
    parts = []

    profile = data.get("profile", {})
    if profile:
        lines = ["## Simons Profil"]
        for k, v in profile.items():
            lines.append(f"- {k}: {v}")
        parts.append("\n".join(lines))

    settings = data.get("settings", {})
    active = []
    for k, v in settings.items():
        if v is True:
            active.append(f"- {k}: aktiv")
        elif v is False or v is None or v == "":
            continue
        else:
            active.append(f"- {k}: {v}")
    if active:
        parts.append("## Einstellungen & Verhaltensregeln\n" + "\n".join(active))

    memory = data.get("memory", {})
    if isinstance(memory, list):
        entries = memory
    elif isinstance(memory, dict):
        entries = [f"{k}: {v}" for k, v in memory.items()]
    else:
        entries = []
    if entries:
        parts.append("## Erinnerungen\n" + "\n".join(f"- {e}" for e in entries))

    return "\n\n".join(parts)
