from datetime import date, datetime
from pathlib import Path
import config

_SECTIONS = ["profile", "settings", "memory", "followups", "context_config"]
_MEMORY_DEFAULT = []
_DICT_DEFAULT = {}

# Lokaler Fallback-Pfad falls Supabase nicht konfiguriert
_LOCAL_DIR = Path.home() / ".jarvis" / "brain"


def _use_supabase() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_KEY)


def _get_client():
    from supabase import create_client
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _default(section: str):
    return _MEMORY_DEFAULT.copy() if section == "memory" else dict(_DICT_DEFAULT)


# ── Supabase I/O ──────────────────────────────────────────────────────────────

def _sb_read(section: str):
    try:
        result = _get_client().table("brain").select("data").eq("section", section).single().execute()
        return result.data["data"] if result.data else _default(section)
    except Exception as e:
        print(f"[brain] Supabase read error ({section}): {e}")
        return _default(section)


def _sb_write(section: str, data):
    try:
        _get_client().table("brain").upsert(
            {"section": section, "data": data},
            on_conflict="section"
        ).execute()
    except Exception as e:
        print(f"[brain] Supabase write error ({section}): {e}")


# ── Lokales Fallback I/O ──────────────────────────────────────────────────────

def _local_path(section: str) -> Path:
    return _LOCAL_DIR / f"{section}.json"


def _local_read(section: str):
    import json
    p = _local_path(section)
    if not p.exists():
        return _default(section)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _default(section)


def _local_write(section: str, data):
    import json
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    _local_path(section).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def _read(section: str):
    if _use_supabase():
        return _sb_read(section)
    return _local_read(section)


def _write(section: str, data):
    if _use_supabase():
        _sb_write(section, data)
    else:
        _local_write(section, data)


def sync():
    """Beim Start: abgelaufene Pausen entfernen und verpasste Routinen flaggen."""
    _check_expirations()
    _check_missed_routines()


def _check_missed_routines():
    settings = _read("settings")
    if not isinstance(settings, dict):
        return
    routines = settings.get("routines", {})
    if not isinstance(routines, dict) or not routines:
        return

    now = datetime.now()
    today_iso = now.date().isoformat()
    now_time = now.strftime("%H:%M")
    _DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    today_day = _DAYS[now.weekday()]

    followups = _read("followups")
    if not isinstance(followups, dict):
        followups = {}

    followups_changed = False
    settings_changed = False

    for rname, rcfg in routines.items():
        if not isinstance(rcfg, dict) or not rcfg.get("active", True):
            continue
        if rcfg.get("last_done") == today_iso:
            continue

        days = rcfg.get("window", {}).get("days", [])
        if days and today_day not in days:
            continue

        window_to = rcfg.get("window", {}).get("to", "23:59")
        cutoff = rcfg.get("carry_over_until", window_to)
        if now_time <= cutoff:
            continue

        deferred = rcfg.get("deferred_until")
        if deferred:
            if now_time < deferred:
                continue
            routines[rname].pop("deferred_until", None)
            settings_changed = True

        followup_key = f"missed_{rname}"
        if followup_key not in followups:
            short_name = rcfg.get("description", rname).split(":")[0]
            followups[followup_key] = {
                "text": f"Heute kein {short_name} — Warum? Nachholen, direkt zum Checkout oder alles erledigt?",
                "mandatory": True,
            }
            followups_changed = True

    if followups_changed:
        _write("followups", followups)
    if settings_changed:
        _write("settings", settings)


def _check_expirations():
    settings = _read("settings")
    if not isinstance(settings, dict):
        return
    today = date.today().isoformat()
    expired = [k for k in settings if k.endswith("_pausiert_bis") and settings[k] <= today]
    if expired:
        for k in expired:
            del settings[k]
        _write("settings", settings)


def load() -> dict:
    return {s: _read(s) for s in _SECTIONS}


def read(section: str, key: str | None = None):
    data = _read(section)
    if key is None:
        return data
    parts = key.split(".", 1)
    if len(parts) == 2 and isinstance(data.get(parts[0]), dict):
        return data[parts[0]].get(parts[1])
    return data.get(key) if isinstance(data, dict) else None


def write(section: str, key: str, value) -> str:
    data = _read(section)

    if section == "memory":
        if not isinstance(data, list):
            data = []
        if isinstance(value, dict):
            data.append(value)
        else:
            data.append({"type": "note", "key": key, "text": str(value)})
        _write(section, data)
        return f"Gespeichert: memory → {value}"

    if not isinstance(data, dict):
        data = {}

    parts = key.split(".", 1)
    if len(parts) == 2:
        if not isinstance(data.get(parts[0]), dict):
            data[parts[0]] = {}
        if value is None:
            data[parts[0]].pop(parts[1], None)
        else:
            data[parts[0]][parts[1]] = value
    else:
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value

    _write(section, data)
    if value is None:
        return f"Gelöscht: {section} → {key}"
    return f"Gespeichert: {section} → {key} = {value}"


# ── System Prompt Section ─────────────────────────────────────────────────────

def _format_month_day(value: str) -> str:
    try:
        month, day = value.split("-")
        return f"{day}.{month}."
    except Exception:
        return value


def build_prompt_section() -> str:
    data = load()
    parts = []

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

    followups = data.get("followups", {})
    if isinstance(followups, dict) and followups:
        today_iso = date.today().isoformat()
        mandatory_items = []
        regular_items = []
        for k, v in followups.items():
            if not v:
                continue
            if isinstance(v, dict):
                due = v.get("due")
                if due and due > today_iso:
                    continue
                text = v.get("text", str(v))
                if v.get("mandatory"):
                    mandatory_items.append(text)
                else:
                    regular_items.append(text)
            else:
                regular_items.append(str(v))
        if mandatory_items:
            lines = ["## PFLICHT-Follow-ups — sofort ansprechen, keine Ausnahme"]
            for item in mandatory_items:
                lines.append(f"- {item}")
            parts.append("\n".join(lines))
        if regular_items:
            lines = ["## Offene Follow-ups — heute aktiv ansprechen"]
            for item in regular_items:
                lines.append(f"- {item}")
            parts.append("\n".join(lines))

    settings = data.get("settings", {})
    behavior = settings.get("behavior", {}) if isinstance(settings, dict) else {}
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

    features = settings.get("features", {}) if isinstance(settings, dict) else {}
    checkin_rules = settings.get("checkin_rules", {}) if isinstance(settings, dict) else {}
    ongoing_reminders = settings.get("ongoing_reminders", {}) if isinstance(settings, dict) else {}
    special_handling = settings.get("special_handling", {}) if isinstance(settings, dict) else {}
    contacts = settings.get("contacts", {}) if isinstance(settings, dict) else {}

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

    known_sections = {"features", "behavior", "checkin_rules", "ongoing_reminders", "special_handling", "contacts"}
    for k, v in (settings.items() if isinstance(settings, dict) else []):
        if k in known_sections:
            continue
        if k.endswith("_pausiert_bis") and v:
            feature = k.replace("_pausiert_bis", "")
            active_rules.append(f"- {feature}: PAUSIERT bis {v} – nicht ansprechen bis dahin")
        elif v and v is not False:
            active_rules.append(f"- {k}: {v}")

    if active_rules:
        parts.append("## Aktive Verhaltensregeln\n" + "\n".join(active_rules))

    routines = settings.get("routines", {}) if isinstance(settings, dict) else {}
    if routines:
        today_iso = date.today().isoformat()
        routine_lines = []
        for rname, rcfg in routines.items():
            if not isinstance(rcfg, dict) or not rcfg.get("active", True):
                continue
            if rcfg.get("last_done") == today_iso:
                continue
            desc = rcfg.get("description", rname)
            window = rcfg.get("window", {})
            days_str = "/".join(window.get("days", [])) if window.get("days") else "täglich"
            time_str = f"{window.get('from', '?')}–{window.get('to', '?')}" if window.get("from") else ""
            prio = rcfg.get("priority", 99)
            deferred = rcfg.get("deferred_until")
            line = f"- [{prio}] {desc}"
            if time_str:
                line += f" ({days_str}, {time_str})"
            if deferred:
                line += f" — verschoben auf {deferred}"
            routine_lines.append(line)
        if routine_lines:
            parts.append("## Aktive Routinen (nach Priorität)\n" + "\n".join(routine_lines))

    memory = data.get("memory", [])
    memory_lines = []
    if isinstance(memory, list):
        for entry in memory:
            if not isinstance(entry, dict):
                memory_lines.append(f"- {entry}")
                continue
            if entry.get("type") == "birthday":
                name = entry.get("name", "Unbekannt")
                ctx = entry.get("context")
                date_val = entry.get("date")
                repeat = entry.get("repeat")
                line = f"- {name}"
                if ctx:
                    line += f" ({ctx})"
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
