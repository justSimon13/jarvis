"""
Personas — Rollendefinition als Datenobjekt.

Siehe docs-draft/JARVIS-Konzept-2026-07-28.md ("Personas — Rolle statt Charakter",
Nachtrag 02.08.) und docs-draft/JARVIS-Datenmodell-und-API.md, Abschnitt `personas`.

Eine Persona steht auf vier Beinen: Arbeitsweise, Werkzeug-Vorauswahl, Fachwissen,
Ton. Dieses Modul hält die drei, die Daten sind — die Arbeitsweise liegt bewusst
als Dokument in der Wissensdatenbank und wird hier nur referenziert.

Der Kernpunkt, und der Grund für scope_tags statt einer Wissensliste
-------------------------------------------------------------------
Eine Persona LÄDT KEIN WISSEN, sie filtert den Index. Eine Liste geladener
Topic-Zusammenfassungen wächst linear in den statischen Prompt hinein: zehn
Themen fahren bei JEDER Nachricht mit, unabhängig vom Gesprächsinhalt. Das
widerspricht Leitregel 2 des Datenmodells ("Dokumente werden gesucht, nicht
mitgeschickt").

Deshalb Etiketten statt Ordner: ein Dokument kann zu mehreren Rollen gehören
(Preisgestaltung betrifft Coach UND Buchhaltung), ohne Kopie. Und ein neues
Dokument taucht bei einer Persona auf, weil es passend beschriftet ist — nicht
weil jemand die Persona bearbeitet hat. Das ist die Eigenschaft, die mit
wachsendem Bestand trägt.

Wohnort ist knowledge_index.db — dieselbe Begründung wie bei `imports`: dort
entstehen `documents`/`document_suggestions`, und die Datei wird vom Backup
bereits erfasst. Eine neue Datenbankdatei wäre stillschweigend ungesichert.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PATH = Path.home() / ".jarvis" / "knowledge_index.db"

# Startbestand, damit die bestehenden Modi nach der Einführung nicht plötzlich
# ohne Persona dastehen. Werkzeuge und Geltungsbereich bleiben leer — leer
# bedeutet ausdrücklich "keine Einschränkung", nicht "nichts".
_SEED = {
    "assistent":  "Organisation: Termine, Todos, Projekte, Tagesablauf.",
    "coach":      "Reflexion und Wachstum: Ziele, Rückblick, Positionierung.",
    "entwickler": "Webentwicklung und alles, was zur Umsetzung gehört — inklusive SEO.",
}


def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personas (
            id          TEXT PRIMARY KEY,   -- entspricht dem Modus: 'coach', 'entwickler'
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            -- JSON-Liste von Werkzeugnamen. LEER = alle, nicht keine. Die
            -- Vorauswahl ist Zuverlässigkeit, keine Berechtigung: Werkzeuge
            -- sind global, siehe Konzept ("Vorauswahl ja, Mauer nein").
            tools       TEXT DEFAULT '[]',
            -- JSON-Liste von Etiketten. Bestimmt, welcher Ausschnitt des
            -- Dokument-Index im Prompt erscheint. LEER = kein Index.
            scope_tags  TEXT DEFAULT '[]',
            -- Zeiger auf das Arbeitsweise-Dokument, Format "topic/file".
            document_id TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _row(r) -> dict:
    data = {
        "id": r[0], "name": r[1], "description": r[2],
        "tools": r[3], "scope_tags": r[4], "document_id": r[5],
        "created_at": r[6], "updated_at": r[7],
    }
    for key in ("tools", "scope_tags"):
        try:
            data[key] = json.loads(data[key] or "[]")
        except Exception:
            data[key] = []
    return data


_COLS = "id, name, description, tools, scope_tags, document_id, created_at, updated_at"


def seed_defaults() -> None:
    """Legt die drei bestehenden Modi als Personas an, falls noch nicht da.
    Idempotent — beim Serverstart aufrufbar."""
    now = datetime.now().isoformat()
    with _get_db() as conn:
        for pid, description in _SEED.items():
            conn.execute(
                "INSERT OR IGNORE INTO personas (id, name, description, tools, scope_tags, "
                "document_id, created_at, updated_at) VALUES (?,?,?,'[]','[]',NULL,?,?)",
                (pid, pid.capitalize(), description, now, now),
            )


def upsert(persona_id: str, name: str | None = None, description: str | None = None,
           tools: list | None = None, scope_tags: list | None = None,
           document_id: str | None = None) -> dict:
    """Anlegen oder ändern. Nicht übergebene Felder bleiben unangetastet —
    damit lässt sich der Geltungsbereich setzen, ohne die Beschreibung zu
    verlieren."""
    now = datetime.now().isoformat()
    existing = get(persona_id)
    with _get_db() as conn:
        if existing:
            fields, values = {}, []
            if name is not None:        fields["name"] = name
            if description is not None: fields["description"] = description
            if tools is not None:       fields["tools"] = json.dumps(tools)
            if scope_tags is not None:  fields["scope_tags"] = json.dumps(scope_tags)
            if document_id is not None: fields["document_id"] = document_id
            fields["updated_at"] = now
            clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE personas SET {clause} WHERE id = ?",
                         (*fields.values(), persona_id))
        else:
            conn.execute(
                "INSERT INTO personas (id, name, description, tools, scope_tags, document_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (persona_id, name or persona_id.capitalize(), description or "",
                 json.dumps(tools or []), json.dumps(scope_tags or []),
                 document_id, now, now),
            )
    return get(persona_id)


# ── Anschluss an den generischen entity_action-Weg (server.py) ───────────────
#
# Zwei Adapter statt einer Sonderbehandlung: der generische Weg ruft beim
# Anlegen fn(**fields) und beim Ändern fn(id, **fields). upsert() erwartet die
# id dagegen als erstes Argument und darf sie nicht zusätzlich in fields sehen.

def create_from_fields(**fields) -> str:
    persona_id = str(fields.pop("id", "") or "").strip().lower()
    if not persona_id:
        raise ValueError("id erforderlich (z.B. 'trainer')")
    if not persona_id.replace("_", "").isalnum():
        raise ValueError(f"Ungültige id: {persona_id!r} — nur Buchstaben, Ziffern, Unterstrich")
    if get(persona_id):
        raise ValueError(f"Persona '{persona_id}' existiert bereits")
    upsert(persona_id, **fields)
    return persona_id


def update_from_fields(persona_id: str, **fields) -> None:
    fields.pop("id", None)
    if not get(persona_id):
        raise ValueError(f"Persona '{persona_id}' nicht gefunden")
    upsert(persona_id, **fields)


def get(persona_id: str) -> dict | None:
    with _get_db() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM personas WHERE id = ?", (persona_id,)).fetchone()
    return _row(r) if r else None


def list_personas() -> list[dict]:
    with _get_db() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM personas ORDER BY id").fetchall()
    return [_row(r) for r in rows]


def delete(persona_id: str) -> None:
    with _get_db() as conn:
        conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))


# ── Index-Ausschnitt ─────────────────────────────────────────────────────────

# Obergrenze für den Index im Prompt. Wird sie erreicht, ist der Geltungsbereich
# zu weit gefasst — dann lieber sichtbar abschneiden als den Prompt unbemerkt
# aufblähen.
_MAX_INDEX_ENTRIES = 60


# ── Arbeitsweise ─────────────────────────────────────────────────────────────

def read_working_method(persona_id: str) -> str:
    """Das Arbeitsweise-Dokument einer Persona als Prompt-Abschnitt.

    `document_id` hat die Form "topic/file" und zeigt in die Wissensdatenbank.
    Nicht gesetzt oder nicht auffindbar → leerer String, der Prompt kommt dann
    ohne aus.

    Anders als der Index-Ausschnitt wird hier der VOLLTEXT eingebettet: eine
    Arbeitsweise soll wirken, nicht gefunden werden. Ein Vorgehen, das das
    Modell erst über search_knowledge holen müsste, wird im Zweifel nicht
    angewendet — und genau darauf zielt das erste der vier Beine aus dem
    Konzept ("Arbeitsweise, das Wertvollste").
    """
    persona = get(persona_id)
    if not persona or not persona.get("document_id"):
        return ""
    zeiger = str(persona["document_id"]).strip()
    if "/" not in zeiger:
        print(f"[personas] '{persona_id}': document_id '{zeiger}' ist kein topic/file", flush=True)
        return ""
    topic, _, datei = zeiger.partition("/")
    try:
        import knowledge
        inhalt = knowledge.read(topic.strip(), datei.strip())
    except Exception as e:
        print(f"[personas] Arbeitsweise '{zeiger}' nicht lesbar: {e}", flush=True)
        return ""
    if not inhalt or not inhalt.strip():
        print(f"[personas] Arbeitsweise '{zeiger}' ist leer", flush=True)
        return ""

    # Frontmatter abschneiden: topic/updated/tags sind Verwaltungsdaten für den
    # Index, im Prompt wären es nur Zeilen, die das Modell nichts angehen — und
    # sie fahren bei JEDEM Gespräch mit.
    text = inhalt.strip()
    if text.startswith("---"):
        ende = text.find("---", 3)
        if ende != -1:
            text = text[ende + 3:].strip()

    if not text:
        print(f"[personas] Arbeitsweise '{zeiger}' enthält nur Frontmatter", flush=True)
        return ""
    return f"## So arbeitest du in dieser Rolle ({persona['name']})\n\n{text}"


# ── Werkzeug-Vorauswahl ──────────────────────────────────────────────────────

# Werkzeuge, die JEDE Persona bekommt — unabhängig von ihrer Liste.
#
# Ohne diesen Kern wäre eine Vorauswahl gefährlich statt hilfreich: eine Persona,
# der jemand versehentlich das Gedächtnis oder den Kalender wegkonfiguriert,
# wirkt kaputt, ohne dass die Ursache sichtbar wäre. Hier steht deshalb alles,
# was JARVIS in JEDEM Gespräch können muss.
#
# Die Vorauswahl ist Zuverlässigkeit, keine Berechtigung ("Vorauswahl ja, Mauer
# nein", Konzept): sie soll die Entscheidung des Modells erleichtern, nicht
# absichern. Wer ein fehlendes Werkzeug braucht, wechselt den Modus.
CORE_TOOLS = {
    # Gedächtnis und Wissen — das Herzstück, nie einschränken
    "read_knowledge", "write_knowledge", "append_knowledge_section", "search_knowledge",
    "brain_read", "brain_write",
    # Organisation: Todos, Projekte, Kontakte, Seiten
    "data_query", "data_write", "data_update", "data_delete",
    "read_seite", "create_seite", "write_seite",
    # Zeit und Termine
    "calendar_query", "calendar_write", "calendar_delete",
    # Nachschlagen
    "web_search",
}


def resolve_tools(persona_id: str, definitions: list[dict]) -> list[dict]:
    """Filtert die Werkzeugliste für eine Persona.

    Regeln, bewusst defensiv:
    - Persona unbekannt oder `tools` leer → ALLE Werkzeuge (bisheriges Verhalten).
      Damit ändert sich nichts, solange niemand eine Liste gesetzt hat.
    - Liste gesetzt → CORE_TOOLS plus die genannten.
    - Namen, die es nicht (mehr) gibt, werden still übergangen. Ein Tippfehler
      oder ein umbenanntes Werkzeug darf nicht dazu führen, dass eine Persona
      plötzlich ohne dasteht.
    - Bliebe nach dem Filtern nichts übrig, gibt es alle zurück — eine kaputte
      Konfiguration soll JARVIS nicht handlungsunfähig machen.
    """
    persona = get(persona_id)
    if not persona:
        return definitions
    gewuenscht = {t.strip() for t in (persona.get("tools") or []) if isinstance(t, str) and t.strip()}
    if not gewuenscht:
        return definitions

    erlaubt = CORE_TOOLS | gewuenscht
    gefiltert = [d for d in definitions if d.get("name") in erlaubt]
    if not gefiltert:
        print(f"[personas] Werkzeug-Vorauswahl für '{persona_id}' ergab nichts — "
              f"nutze alle {len(definitions)}", flush=True)
        return definitions

    unbekannt = gewuenscht - {d.get("name") for d in definitions}
    if unbekannt:
        print(f"[personas] '{persona_id}': unbekannte Werkzeuge in der Vorauswahl "
              f"übergangen: {', '.join(sorted(unbekannt))}", flush=True)
    return gefiltert


def build_index_section(persona_id: str, entries: list[dict]) -> str:
    """Baut das Inhaltsverzeichnis für eine Persona aus dem Wissens-Index.

    entries: Ergebnis von knowledge.list_available() — [{path, topic, tags, summary}].
    Gefiltert wird über die Etiketten der Persona. Ausgegeben werden Titel und
    Kurzfassung, NICHT der Inhalt: das Modell soll wissen, dass es etwas gibt,
    und es bei Bedarf über search_knowledge holen.
    """
    persona = get(persona_id)
    if not persona or not persona["scope_tags"]:
        return ""
    wanted = {t.strip().lower() for t in persona["scope_tags"] if t and t.strip()}
    if not wanted:
        return ""

    lines = []
    for entry in entries:
        tags = {t.strip().lower() for t in (entry.get("tags") or "").split(",") if t.strip()}
        # Das Topic zählt als Etikett mit — sonst müsste jeder Bestand, der vor
        # der Einführung von Etiketten entstanden ist, erst nachbeschriftet
        # werden, um überhaupt auffindbar zu sein.
        topic = (entry.get("topic") or "").strip().lower()
        if topic:
            tags.add(topic)
        if not (tags & wanted):
            continue
        summary = (entry.get("summary") or "").strip().replace("\n", " ")
        lines.append(f"- {entry['path']}" + (f" — {summary}" if summary else ""))

    if not lines:
        return ""
    truncated = len(lines) > _MAX_INDEX_ENTRIES
    shown = lines[:_MAX_INDEX_ENTRIES]
    header = (f"## Verfügbares Wissen ({persona['name']})\n"
              "Diese Dokumente stehen bereit. Inhalte bei Bedarf mit read_knowledge "
              "oder search_knowledge holen — sie sind hier NICHT enthalten.")
    footer = (f"\n… und {len(lines) - _MAX_INDEX_ENTRIES} weitere "
              "(Geltungsbereich der Persona ist sehr weit gefasst)") if truncated else ""
    return header + "\n" + "\n".join(shown) + footer
