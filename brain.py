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
    if key is None:
        return data
    # Dot-Notation: "contacts.email_vip" → data["contacts"]["email_vip"]
    parts = key.split(".", 1)
    if len(parts) == 2 and isinstance(data.get(parts[0]), dict):
        return data[parts[0]].get(parts[1])
    return data.get(key)


def write(section: str, key: str, value) -> str:
    data = _read(section)

    if section == "memory":
        # Memory ist eine Liste — neuen Eintrag anhängen
        if not isinstance(data, list):
            data = []
        if isinstance(value, dict):
            data.append(value)
        else:
            data.append({"type": "note", "key": key, "text": str(value)})
        _write(section, data)
        _git_push(f"JARVIS: memory Eintrag hinzugefügt")
        return f"Gespeichert: memory → {value}"

    # Dot-Notation für nested Settings: "contacts.email_vip"
    parts = key.split(".", 1)
    if len(parts) == 2 and isinstance(data.get(parts[0]), dict):
        data[parts[0]][parts[1]] = value
    else:
        data[key] = value

    _write(section, data)
    _git_push(f"JARVIS: {section}.{key} aktualisiert")
    return f"Gespeichert: {section} → {key} = {value}"


def _format_month_day(value: str) -> str:
    try:
        month, day = value.split("-")
        return f"{day}.{month}."
    except Exception:
        return value


def build_prompt_section() -> str:
    data = load()
    parts = []

    # 1. Profil
    profile = data.get("profile", {})
    if profile:
        ordered_keys = [
            "name", "standort", "alter", "lebenssituation", "anstellung", "selbständig",
            "arbeitsrhythmus", "daily_rhythmus", "freelancing_positionierung",
            "freelancing_stack", "freelancing_zielkunden", "freelancing_rate",
            "freelancing_kanaele", "langfristige_ziele", "btc_kontext",
            "btc_bestand", "btc_investiert",
        ]
        label_map = {
            "name": "Name", "standort": "Standort", "alter": "Alter",
            "lebenssituation": "Lebenssituation", "anstellung": "Anstellung",
            "selbständig": "Selbständigkeit", "arbeitsrhythmus": "Arbeitsrhythmus",
            "daily_rhythmus": "Tagesrhythmus", "freelancing_positionierung": "Freelancing-Positionierung",
            "freelancing_stack": "Freelancing-Stack", "freelancing_zielkunden": "Zielkunden",
            "freelancing_rate": "Stundensatz", "freelancing_kanaele": "Akquise-Kanäle",
            "langfristige_ziele": "Langfristige Ziele", "btc_kontext": "BTC-Kontext",
            "btc_bestand": "BTC-Bestand", "btc_investiert": "In BTC investiert",
        }
        lines = ["## Wer Simon ist"]
        for key in ordered_keys:
            value = profile.get(key)
            if value:
                lines.append(f"- {label_map.get(key, key)}: {value}")
        for key, value in profile.items():
            if key not in ordered_keys and value:
                lines.append(f"- {key}: {value}")
        parts.append("\n".join(lines))

    # 2. Settings
    settings = data.get("settings", {})

    behavior = settings.get("behavior", {})
    if behavior:
        lines = ["## Wie du dich verhalten sollst"]
        label_map_b = {
            "conversation_style": "Sprachstil",
            "response_prioritization": "Priorisierung",
            "reminder_style": "Erinnerungen",
            "checkin_density": "Check-in-Stil",
        }
        for k, v in behavior.items():
            if v:
                lines.append(f"- {label_map_b.get(k, k)}: {v}")
        parts.append("\n".join(lines))

    features = settings.get("features", {})
    checkin_rules = settings.get("checkin_rules", {})
    ongoing_reminders = settings.get("ongoing_reminders", {})
    special_handling = settings.get("special_handling", {})
    contacts = settings.get("contacts", {})

    active_rules = []
    if features.get("morning_checkin"):
        active_rules.append("- Morning Check-in ist aktiv.")
    if features.get("evening_checkout"):
        active_rules.append("- Evening Checkout ist aktiv.")
    if features.get("daily_football_fact"):
        active_rules.append("- Daily Football Fact ist aktiv.")

    if checkin_rules.get("todos"):
        active_rules.append(f"- Todo-Regel: {checkin_rules['todos']}")
    if checkin_rules.get("projekte"):
        active_rules.append(f"- Projekt-Regel: {checkin_rules['projekte']}")
    if checkin_rules.get("btc"):
        active_rules.append(f"- BTC-Regel: {checkin_rules['btc']}")
    if checkin_rules.get("konzepte"):
        active_rules.append(f"- Konzepte-Regel: {checkin_rules['konzepte']}")
    if checkin_rules.get("abschluss"):
        active_rules.append(f"- Abschluss-Regel: {checkin_rules['abschluss']}")

    for k, v in ongoing_reminders.items():
        if v:
            active_rules.append(f"- Laufende Erinnerung: {v}")
    for k, v in special_handling.items():
        if v:
            active_rules.append(f"- Regel ({k}): {v}")
    if contacts.get("email_vip"):
        vip_list = ", ".join(contacts["email_vip"])
        active_rules.append(f"- VIP-E-Mail-Kontakte: {vip_list}")

    # Flache Top-Level-Keys (z.B. _pausiert_bis) als Fallback
    known_sections = {"features", "behavior", "checkin_rules", "ongoing_reminders", "special_handling", "contacts"}
    for k, v in settings.items():
        if k in known_sections:
            continue
        if k.endswith("_pausiert_bis") and v:
            feature = k.replace("_pausiert_bis", "")
            active_rules.append(f"- {feature}: PAUSIERT bis {v} – nicht ansprechen bis dahin")
        elif v and v is not False:
            active_rules.append(f"- {k}: {v}")

    if active_rules:
        parts.append("## Aktive Verhaltensregeln\n" + "\n".join(active_rules))

    # 3. Erinnerungen
    memory = data.get("memory", [])
    memory_lines = []
    if isinstance(memory, list):
        for entry in memory:
            if not isinstance(entry, dict):
                memory_lines.append(f"- {entry}")
                continue
            if entry.get("type") == "birthday":
                name = entry.get("name", "Unbekannt")
                context = entry.get("context")
                date_val = entry.get("date")
                repeat = entry.get("repeat")
                line = f"- {name}"
                if context:
                    line += f" ({context})"
                if date_val:
                    line += f" hat am {_format_month_day(date_val)} Geburtstag"
                if repeat == "yearly":
                    line += ", jährlich erinnern"
                line += "."
                memory_lines.append(line)
            else:
                text = entry.get("text") or entry.get("note") or str(entry)
                memory_lines.append(f"- {text}")
    elif isinstance(memory, dict):
        for v in memory.values():
            memory_lines.append(f"- {v}")
    if memory_lines:
        parts.append("## Wichtige Erinnerungen\n" + "\n".join(memory_lines))

    return "\n\n".join(parts)
