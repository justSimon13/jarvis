import json
import subprocess
from datetime import date
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


def _check_expirations():
    """Keys mit Suffix '_pausiert_bis' prüfen und abgelaufene entfernen."""
    settings = _read("settings")
    today = date.today().isoformat()
    expired = [k for k in settings if k.endswith("_pausiert_bis") and settings[k] <= today]
    if expired:
        for k in expired:
            del settings[k]
        _write("settings", settings)
        _git_push("JARVIS: abgelaufene Pausen entfernt")


def sync():
    """Aktuellen Stand vom Remote holen und abgelaufene Pausen prüfen."""
    cwd = Path(__file__).parent
    try:
        subprocess.run(["git", "pull", "--ff-only"], cwd=cwd, capture_output=True)
    except Exception:
        pass
    _check_expirations()


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

    # 1. Profil als Fließtext
    p = data.get("profile", {})
    if p:
        known_keys = {
            "name", "standort", "alter", "lebenssituation", "anstellung", "selbständig",
            "rhythmus_konzentration", "freelancing_positionierung", "freelancing_stack",
            "freelancing_zielkunden", "freelancing_rate", "freelancing_kanaele",
            "langfristige_ziele", "btc_bestand", "btc_investiert", "btc_strategie",
        }
        lines = ["## Wer Simon ist"]
        if p.get("name") and p.get("standort"):
            intro = f"{p['name']}"
            if p.get("alter"):
                intro += f" ({p['alter']})"
            intro += f" lebt in {p['standort']}."
            if p.get("lebenssituation"):
                ls = p["lebenssituation"]
                intro += " " + ls[0].upper() + ls[1:] + "."
            lines.append(intro)
        if p.get("anstellung"):
            lines.append(p["anstellung"] + ".")
        if p.get("selbständig"):
            lines.append(p["selbständig"] + ".")
        if p.get("rhythmus_konzentration"):
            lines.append(p["rhythmus_konzentration"].capitalize() + ".")
        if p.get("freelancing_positionierung"):
            lines.append("Freelancing: " + p["freelancing_positionierung"])
        if p.get("freelancing_stack"):
            lines.append("Stack: " + p["freelancing_stack"])
        if p.get("freelancing_zielkunden"):
            lines.append("Zielkunden: " + p["freelancing_zielkunden"])
        if p.get("freelancing_rate"):
            lines.append("Rate: " + p["freelancing_rate"])
        if p.get("freelancing_kanaele"):
            lines.append("Kanäle: " + p["freelancing_kanaele"])
        if p.get("langfristige_ziele"):
            lines.append("Langfristig: " + p["langfristige_ziele"])
        if p.get("btc_bestand"):
            lines.append(
                f"Bitcoin: {p['btc_bestand']}, investiert {p.get('btc_investiert', '')}. "
                f"{p.get('btc_strategie', '')}"
            )
        for k, v in p.items():
            if k not in known_keys:
                lines.append(f"- {k}: {v}")
        parts.append("\n".join(lines))

    # 2. Settings kategorisieren
    s = data.get("settings", {})
    features, rules, checkin_rules, reminders, paused = [], [], [], [], []

    for k, v in s.items():
        if k == "email_vip":
            continue
        if k.endswith("_pausiert_bis"):
            feature = k.replace("_pausiert_bis", "")
            paused.append(f"- {feature}: PAUSIERT bis {v} – nicht ansprechen bis dahin")
        elif v is True:
            features.append(f"- {k}: aktiv")
        elif v is False or v is None or v == "":
            continue
        elif k.startswith("checkin_"):
            label = k.replace("checkin_", "")
            checkin_rules.append(f"- {label}: {v}")
        elif k.endswith("_reminder"):
            reminders.append(f"- {v}")
        elif k.startswith("rule_"):
            rules.append(f"- {v}")
        else:
            rules.append(f"- {k}: {v}")

    if features or rules:
        parts.append("## Verhaltensregeln\n" + "\n".join(features + rules))

    if checkin_rules:
        parts.append("## Check-in Regeln\n" + "\n".join(checkin_rules))

    if reminders or paused:
        parts.append("## Aktive Reminder\n" + "\n".join(reminders + paused))

    # 3. Erinnerungen
    memory = data.get("memory", {})
    if isinstance(memory, list):
        entries = [str(e) for e in memory]
    elif isinstance(memory, dict):
        entries = list(memory.values())
    else:
        entries = []
    if entries:
        parts.append("## Erinnerungen\n" + "\n".join(f"- {e}" for e in entries))

    return "\n\n".join(parts)
