from __future__ import annotations
from datetime import date, datetime, timedelta
import config
import brain
import knowledge
import local_data
from services import btc
from services import calendar as calendar_service
import session_memory


_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]


# ── Context Modularisierung ───────────────────────────────────────────────────

# Keywords → Modul
_KEYWORD_MAP: dict[str, str] = {
    "todo": "todos",        "aufgabe": "todos",      "erledigt": "todos",
    "task": "todos",        "offen": "todos",

    "termin": "calendar",   "kalender": "calendar",  "wann": "calendar",
    "meeting": "calendar",  "heute": "calendar",     "morgen": "calendar",
    "appointment": "calendar",

    "projekt": "projects",  "freelancing": "projects", "kunde": "projects",
    "auftrag": "projects",  "arbeit": "projects",

    "bitcoin": "btc",       "btc": "btc",            "kurs": "btc",
    "crypto": "btc",        "preis": "btc",          "portfolio": "btc",
}

_CHECKIN_KEYWORDS = {"check-in", "checkin", "guten morgen", "morgen check",
                     "morning", "check in", "tagesstart"}

# Welche Module jeder Modus standardmäßig lädt (ohne Keyword-Trigger)
_MODE_DEFAULT_MODULES: dict[str, set[str]] = {
    "assistent":  {"todos", "calendar", "btc"},
    "coach":      {"todos", "calendar"},
    "entwickler": {"todos", "calendar"},
}


def detect_modules(first_message: str, mode: str = "assistent") -> set[str]:
    """
    Erkennt welche Kontext-Module für diese Session geladen werden sollen.
    Basis: Mode-Defaults + Keyword-Erkennung auf der ersten User-Message.
    Fallback auf vollständigen Kontext wenn Message sehr kurz/unklar ist.
    """
    # Kurze/unklare Message → vollständiger Kontext als Fallback
    if len(first_message.strip()) < 8:
        return {"todos", "calendar", "btc", "projects"}

    modules: set[str] = set(_MODE_DEFAULT_MODULES.get(mode, {"todos", "calendar", "btc"}))

    text_lower = first_message.lower()

    # Check-in → alles laden
    if any(kw in text_lower for kw in _CHECKIN_KEYWORDS):
        return {"todos", "calendar", "btc", "projects"}

    # Keyword-basiert zusätzliche Module laden
    for keyword, module in _KEYWORD_MAP.items():
        if keyword in text_lower:
            modules.add(module)

    return modules


def build_static_prompt(mode: str = "assistent", active_modules: set[str] | None = None) -> str:
    """
    Stabiler Teil — für Prompt Caching geeignet. Ändert sich höchstens alle 15 min.
    active_modules steuert welche Kontext-Sektionen geladen werden.
    None = alles laden (Fallback/Rückwärtskompatibilität).
    """
    load_all = active_modules is None
    modules = active_modules or {"todos", "calendar", "btc", "projects"}

    modules_prompt = brain.build_modules_prompt(mode)
    parts = [modules_prompt if modules_prompt else "Du bist J.A.R.V.I.S., der persönliche KI-Assistent von Simon Fischer. Antworte auf Deutsch."]

    # ── Knowledge: simon/_core.md — immer laden ───────────────────────────────
    core = knowledge.read("simon", "_core")
    if core:
        parts.append(core)

    # ── Knowledge: Topic-Summary für aktiven Modus ────────────────────────────
    modules_data = brain.read("modules") or {}
    modes_data   = modules_data.get("modes", {}) if isinstance(modules_data, dict) else {}
    mode_cfg     = modes_data.get(mode, {}) if isinstance(modes_data, dict) else {}
    topic_names  = mode_cfg.get("knowledge_topics", []) if isinstance(mode_cfg, dict) else []
    for topic in topic_names:
        summary = knowledge.read_summary(topic)
        if summary:
            parts.append(summary)

    # ── Persona: Ausschnitt des Dokument-Index ────────────────────────────────
    # Additiv NEBEN knowledge_topics oben, nicht als Ersatz — beide Wege können
    # nebeneinander bestehen, bis der alte nachweislich nicht mehr gebraucht
    # wird. Unterschied: knowledge_topics LÄDT ganze Topic-Zusammenfassungen in
    # den Prompt (wächst linear mit jedem Thema), der Index nennt nur Titel und
    # Kurzfassung und überlässt das Holen dem Modell (siehe Leitregel 2 im
    # Datenmodell: Dokumente werden gesucht, nicht mitgeschickt).
    try:
        from services import personas as personas_service

        # Arbeitsweise der Rolle — als VOLLTEXT, nicht als Index-Eintrag.
        #
        # Bewusst der einzige Teil, der komplett mitfährt: der Index nennt nur
        # Titel, damit die Wissensdatenbank wachsen kann ohne die Gespräche zu
        # verteuern (Leitregel 2). Die Arbeitsweise ist die Ausnahme, weil sie
        # WIRKEN soll, nicht gefunden werden — ein Vorgehen, das das Modell erst
        # suchen müsste, wird im Zweifel nicht angewendet. Sie ist kurz und
        # ändert sich selten, taugt also für den gecachten Prompt-Teil.
        #
        # Steht als Dokument in der Wissensdatenbank statt als Feld an der
        # Persona: dann ist sie lesbar, im Gespräch änderbar und hat eine
        # Historie (Konzept, "Wissen steuert Ausführung").
        arbeitsweise = personas_service.read_working_method(mode)
        if arbeitsweise:
            parts.append(arbeitsweise)

        index_section = personas_service.build_index_section(mode, knowledge.list_available())
        if index_section:
            parts.append(index_section)
    except Exception as e:
        # Ein Fehler hier darf nie den ganzen Prompt verhindern — dann
        # antwortet JARVIS lieber ohne Arbeitsweise/Verzeichnis als gar nicht.
        print(f"[context] Persona-Teil übersprungen: {e}", flush=True)

    brain_section = brain.build_prompt_section(mode)
    if brain_section:
        parts.append(brain_section)

    cc = brain.read("context_config") or {}
    if not isinstance(cc, dict):
        cc = {}
    cc_todos = cc.get("todos", {})
    cc_proj = cc.get("projekte", {})
    proj_extra = cc_proj.get("extra_felder", [])

    todos = local_data.list_todos(
        tage_zurueck=cc_todos.get("tage_zurueck", 7),
        max_results=cc_todos.get("max", 20),
    ) if "todos" in modules else []
    projekte = local_data.list_projekte(
        status_filter=cc_proj.get("status_filter") or ["In Bearbeitung", "Planung", "Angebot"],
    ) if "projects" in modules else []

    today = date.today()

    if todos:
        overdue, today_todos, future_todos, undated = [], [], [], []
        for t in todos:
            datum = t.get("datum")
            name = t.get("name")
            status = t.get("status") or ""
            if status in ("Erledigt", "Archiviert"):
                continue
            prio = t.get("prioritaet")
            prio_str = f", {prio}" if prio else ""
            if datum:
                try:
                    todo_date = date.fromisoformat(datum)
                    entry = f"- [{datum}] {name} ({status}{prio_str})"
                    if todo_date < today:
                        overdue.append(f"- ÜBERFÄLLIG ({datum}): {name}{prio_str}")
                    elif todo_date == today:
                        today_todos.append(entry)
                    else:
                        future_todos.append(entry)
                except ValueError:
                    undated.append(f"- {name} ({status}{prio_str})")
            else:
                undated.append(f"- {name} ({status})")
        lines = []
        if overdue:
            lines.append("## Überfällige Todos")
            lines.extend(overdue)
        if today_todos:
            lines.append("## Todos heute")
            lines.extend(today_todos)
        if future_todos:
            lines.append("## Geplante Todos (nicht heute)")
            lines.extend(future_todos)
        if undated:
            lines.append("## Todos ohne Datum")
            lines.extend(undated)
        if lines:
            parts.append("\n".join(lines))

    if projekte:
        lines = ["## Aktive Projekte"]
        for p in projekte:
            meta_parts = [p["status"]]
            if "typ" in proj_extra and p.get("typ"):
                meta_parts.append(p["typ"])
            line = f"- {p['name']} ({', '.join(meta_parts)})"
            if p.get("beschreibung"):
                line += f": {p['beschreibung']}"
            lines.append(line)
        parts.append("\n".join(lines))

    loaded = sorted(modules & {"todos", "calendar", "btc", "projects"})
    print(f"[context] Module geladen: {loaded}", flush=True)

    return "\n\n".join(parts)


def build_dynamic_prompt(room: str | None = None, active_modules: set[str] | None = None) -> str:
    """Volatiler Teil — Uhrzeit, BTC, Kalender, Raum. Nie gecacht."""
    modules = active_modules or {"calendar", "btc"}

    now = datetime.now()
    hour = now.hour
    today = now.date()
    today_str = f"{_WOCHENTAGE[today.weekday()]}, {today.day}. {_MONATE[today.month - 1]} {today.year}"

    if 5 <= hour < 11:
        tageszeit = "Morgen"
    elif 11 <= hour < 14:
        tageszeit = "Mittag"
    elif 14 <= hour < 18:
        tageszeit = "Nachmittag"
    elif 18 <= hour < 22:
        tageszeit = "Abend"
    else:
        tageszeit = "Nacht"

    time_line = f"Aktuelle Zeit: {today_str}, {now.strftime('%H:%M')} ({tageszeit})"
    if room:
        time_line += f"\nAktueller Raum: {room}"
    parts = [time_line]

    if "calendar" in modules:
        cal = calendar_service.format_for_prompt()
        if cal:
            parts.append(cal)

    if "btc" in modules:
        btc_str = btc.format_for_prompt()
        if btc_str:
            parts.append(btc_str)

    return "\n\n".join(parts)
