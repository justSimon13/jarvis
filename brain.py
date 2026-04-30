import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

_SECTIONS = ["profile", "behavior", "memory", "followups", "events", "modules", "config"]
_DB_PATH = Path.home() / ".jarvis" / "brain.db"


# ── SQLite I/O ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain (
            section TEXT PRIMARY KEY,
            data    TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()
    return conn


def _read(section: str):
    with _get_db() as conn:
        row = conn.execute(
            "SELECT data FROM brain WHERE section = ?", (section,)
        ).fetchone()
    if not row:
        return [] if section == "memory" else {}
    try:
        return json.loads(row[0])
    except Exception:
        return [] if section == "memory" else {}


def _write(section: str, data):
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO brain (section, data) VALUES (?, ?)",
            (section, json.dumps(data, ensure_ascii=False))
        )


# ── Public API ────────────────────────────────────────────────────────────────

def get_config(key: str, default=None):
    """Liest einen Wert aus brain.config. Unterstützt dot-notation (z.B. 'notion.todos')."""
    data = _read("config")
    if not isinstance(data, dict):
        return default
    parts = key.split(".", 1)
    if len(parts) == 2:
        sub = data.get(parts[0])
        if isinstance(sub, dict):
            return sub.get(parts[1], default)
        return default
    return data.get(key, default)


def _seed_notion_config():
    """Schreibt Notion-IDs und Konfiguration aus Env-Vars in brain.config (idempotent)."""
    import os
    data = _read("config")
    if not isinstance(data, dict):
        data = {}

    notion = data.get("notion", {})
    if not isinstance(notion, dict):
        notion = {}

    changed = False
    ids = {
        "todos_db_id": os.getenv("NOTION_TODOS_DB_ID", "10ab63fa-fc26-80f5-9865-cf57555d8002"),
        "projekte_db_id": os.getenv("NOTION_PROJEKTE_DB_ID", "194b63fa-fc26-80d1-9832-dceb4301afd3"),
        "konzepte_db_id": os.getenv("NOTION_KONZEPTE_DB_ID", "19fb63fa-fc26-80d3-807c-ffba582e38c0"),
        "kontakte_db_id": os.getenv("NOTION_KONTAKTE_DB_ID", "1a4b63fa-fc26-808c-ad83-e4973e38f570"),
    }
    for k, v in ids.items():
        if k not in notion:
            notion[k] = v
            changed = True

    weather_city = os.getenv("WEATHER_CITY", "Stuttgart")
    if "weather_city" not in data:
        data["weather_city"] = weather_city
        changed = True

    if changed:
        data["notion"] = notion
        _write("config", data)


def _seed_modules_quick_actions():
    """Setzt Standard-Quick-Action-IDs in brain.modules.modes wenn noch nicht vorhanden."""
    modules = _read("modules")
    if not isinstance(modules, dict):
        return
    modes = modules.get("modes", {})
    if not isinstance(modes, dict):
        return
    defaults = {
        "assistent": ["alarm", "todo_add", "checkin"],
        "coach":     ["wochenreview", "fortschritt", "ziel_setzen"],
        "fokus":     ["timer", "naechstes_event"],
    }
    changed = False
    for mode_name, mode_cfg in modes.items():
        if isinstance(mode_cfg, dict) and "quick_actions" not in mode_cfg:
            mode_cfg["quick_actions"] = defaults.get(mode_name, [])
            changed = True
    if changed:
        modules["modes"] = modes
        _write("modules", modules)


def _seed_modules_cards():
    """Setzt Standard-Card-IDs in brain.modules.modes wenn noch nicht vorhanden."""
    modules = _read("modules")
    if not isinstance(modules, dict):
        return
    modes = modules.get("modes", {})
    if not isinstance(modes, dict):
        return
    defaults = {
        "assistent": ["transcript", "btc", "weather", "todos", "calendar"],
        "coach":     ["todos", "calendar"],
        "fokus":     [],
    }
    changed = False
    for mode_name, mode_cfg in modes.items():
        if isinstance(mode_cfg, dict) and "cards" not in mode_cfg:
            mode_cfg["cards"] = defaults.get(mode_name, ["todos", "calendar"])
            changed = True
    if changed:
        modules["modes"] = modes
        _write("modules", modules)


def sync():
    """Beim Start: Migration, abgelaufene Pausen entfernen, verpasste Routinen flaggen."""
    migrate_sections()
    _seed_notion_config()
    _seed_modules_quick_actions()
    _seed_modules_cards()
    _check_expirations()
    check_missed_routines()


def migrate_sections():
    """
    Einmalige Migration von alten Sections (settings, context_config) zu neuen
    (behavior, events, config, modules). Läuft idempotent via _migrated_v2-Flag.
    """
    existing_config = _read("config")
    if isinstance(existing_config, dict) and existing_config.get("_migrated_v2"):
        return

    config_data = dict(existing_config) if isinstance(existing_config, dict) else {}

    # ── settings → behavior / events / config ─────────────────────────────────
    settings = _read("settings")
    if isinstance(settings, dict) and settings:

        # behavior: settings.behavior + settings.special_handling
        behavior_data = dict(settings.get("behavior", {}))
        special_handling = settings.get("special_handling", {})
        if isinstance(special_handling, dict) and special_handling:
            behavior_data["special_handling"] = special_handling
        if behavior_data:
            _write("behavior", behavior_data)

        # events: routines + features + checkin_rules + ongoing_reminders + pauses
        events_data: dict = {}
        for key in ("routines", "features", "checkin_rules", "ongoing_reminders"):
            if settings.get(key):
                events_data[key] = settings[key]
        for key, value in settings.items():
            if key.endswith("_pausiert_bis") and value:
                events_data[key] = value
        _write("events", events_data)

        # config: contacts + remaining loose keys
        if settings.get("contacts"):
            config_data["contacts"] = settings["contacts"]
        known_keys = {
            "behavior", "routines", "features", "checkin_rules",
            "ongoing_reminders", "special_handling", "contacts",
        }
        for key, value in settings.items():
            if key not in known_keys and not key.endswith("_pausiert_bis") and value:
                config_data[key] = value

    # ── context_config → config.notion ────────────────────────────────────────
    context_config = _read("context_config")
    if isinstance(context_config, dict) and context_config:
        config_data["notion"] = context_config

    # ── Seed brain.modules with SYSTEM_PROMPT_BASE ────────────────────────────
    modules = _read("modules")
    if not isinstance(modules, dict) or not modules:
        try:
            from config import SYSTEM_PROMPT_BASE
            _write("modules", {
                "base": {"identity": SYSTEM_PROMPT_BASE, "rules": []},
                "modes": {
                    "assistent": {"description": "Standard-Assistent", "prompt": ""},
                    "coach": {
                        "description": "Performance-Coach",
                        "prompt": "Du agierst als direkter Performance-Coach. Keine Ausreden, klare Fragen.",
                    },
                    "fokus": {
                        "description": "Fokus-Modus",
                        "prompt": "Minimalmodus. Kurze, direkte Antworten. Kein Smalltalk.",
                    },
                },
            })
        except ImportError:
            pass

    config_data["_migrated_v2"] = True
    _write("config", config_data)
    print("[brain] Migration v2 abgeschlossen.", flush=True)


def check_missed_routines():
    config_data = _read("config")
    migration_done = isinstance(config_data, dict) and config_data.get("_migrated_v2")
    source_section = "events" if migration_done else "settings"
    source = _read(source_section)
    if not isinstance(source, dict):
        return

    routines = source.get("routines", {})
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
    source_changed = False

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
            source_changed = True

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
    if source_changed:
        _write(source_section, source)


def _check_expirations():
    config_data = _read("config")
    migration_done = isinstance(config_data, dict) and config_data.get("_migrated_v2")

    # Check new sections for pauses
    for section in (["events"] if migration_done else ["settings"]):
        data = _read(section)
        if not isinstance(data, dict):
            continue
        today = date.today().isoformat()
        expired = [k for k in data if k.endswith("_pausiert_bis") and data[k] <= today]
        if expired:
            for k in expired:
                del data[k]
            _write(section, data)


def load() -> dict:
    return {s: _read(s) for s in _SECTIONS}


def read(section: str, key: str | None = None):
    data = _read(section)
    if key is None:
        return data
    parts = key.split(".", 1)
    if len(parts) == 2 and isinstance(data, dict) and isinstance(data.get(parts[0]), dict):
        return data[parts[0]].get(parts[1])
    return data.get(key) if isinstance(data, dict) else None


def write(section: str, key: str, value) -> str:
    # Redirect legacy section names to new ones
    _REDIRECTS = {"context_config": "config"}
    section = _REDIRECTS.get(section, section)

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


# ── System Prompt ─────────────────────────────────────────────────────────────

def _format_month_day(value: str) -> str:
    try:
        month, day = value.split("-")
        return f"{day}.{month}."
    except Exception:
        return value


def build_modules_prompt(mode: str = "assistent") -> str:
    """Gibt den Persönlichkeits-/Identitäts-Teil aus brain.modules zurück."""
    modules = _read("modules")
    if not isinstance(modules, dict) or not modules:
        return ""

    parts = []
    base = modules.get("base", {})
    if isinstance(base, dict):
        identity = base.get("identity", "")
        rules = base.get("rules", [])
        if identity:
            parts.append(identity)
        if isinstance(rules, list):
            for r in rules:
                if r:
                    parts.append(f"- {r}")

    modes = modules.get("modes", {})
    if isinstance(modes, dict):
        mode_cfg = modes.get(mode, {})
        if isinstance(mode_cfg, dict) and mode_cfg.get("prompt"):
            desc = mode_cfg.get("description", mode)
            parts.append(f"\n## Modus: {desc}\n{mode_cfg['prompt']}")

    return "\n".join(parts)


def build_prompt_section(mode: str = "assistent") -> str:
    """Gibt den Daten-Teil des System-Prompts zurück (Profile, Behavior, Followups, Events, Memory)."""
    data = load()
    parts = []

    # ── Profile (vollständig dynamisch, kein hardcoded Schema) ────────────────
    profile = data.get("profile", {})
    if isinstance(profile, dict) and profile:
        lines = ["## Wer Simon ist"]
        for key, value in profile.items():
            if value:
                lines.append(f"- {key.replace('_', ' ')}: {value}")
        parts.append("\n".join(lines))

    # ── Followups ─────────────────────────────────────────────────────────────
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

    # ── Behavior ──────────────────────────────────────────────────────────────
    behavior = data.get("behavior", {})
    # Fallback: settings.behavior (vor Migration oder wenn behavior-Section leer)
    if not isinstance(behavior, dict) or not behavior:
        settings_fb = _read("settings")
        behavior = settings_fb.get("behavior", {}) if isinstance(settings_fb, dict) else {}
    if behavior:
        lines = ["## Wie du dich verhalten sollst"]
        for k, v in behavior.items():
            if k == "special_handling" and isinstance(v, dict):
                for sk, sv in v.items():
                    if sv:
                        lines.append(f"- Sonderregel ({sk}): {sv}")
            elif v:
                lines.append(f"- {k.replace('_', ' ')}: {v}")
        parts.append("\n".join(lines))

    # ── Events (Regeln, Routinen) ──────────────────────────────────────────────
    events = data.get("events", {})
    # Fallback: settings (vor Migration)
    if not isinstance(events, dict) or not events:
        events = _read("settings")
        if not isinstance(events, dict):
            events = {}

    active_rules = []
    features = events.get("features", {})
    if isinstance(features, dict):
        for k, v in features.items():
            if v and v is not False:
                active_rules.append(f"- {k.replace('_', ' ')}: aktiv")

    checkin_rules = events.get("checkin_rules", {})
    if isinstance(checkin_rules, dict):
        for k, v in checkin_rules.items():
            if v:
                active_rules.append(f"- {k.replace('_', ' ')}-Regel: {v}")

    ongoing_reminders = events.get("ongoing_reminders", {})
    if isinstance(ongoing_reminders, dict):
        for k, v in ongoing_reminders.items():
            if v:
                active_rules.append(f"- Laufende Erinnerung: {v}")

    for k, v in events.items():
        if k.endswith("_pausiert_bis") and v:
            feature = k.replace("_pausiert_bis", "")
            active_rules.append(f"- {feature}: PAUSIERT bis {v} – nicht ansprechen bis dahin")

    if active_rules:
        parts.append("## Aktive Verhaltensregeln\n" + "\n".join(active_rules))

    routines = events.get("routines", {})
    if isinstance(routines, dict) and routines:
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
            time_str = (
                f"{window.get('from', '?')}–{window.get('to', '?')}"
                if window.get("from") else ""
            )
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

    # ── Config: Contacts ──────────────────────────────────────────────────────
    config_section = data.get("config", {})
    if not isinstance(config_section, dict):
        config_section = {}
    contacts = config_section.get("contacts", {})
    # Fallback: settings.contacts
    if not contacts:
        settings_fb = _read("settings")
        contacts = settings_fb.get("contacts", {}) if isinstance(settings_fb, dict) else {}
    if isinstance(contacts, dict) and contacts.get("email_vip"):
        vip_list = ", ".join(contacts["email_vip"])
        parts.append(f"## Kontakte\n- VIP-E-Mail-Kontakte: {vip_list}")

    # ── Memory ────────────────────────────────────────────────────────────────
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
