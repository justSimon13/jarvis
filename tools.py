import json
import notion_service
import brain

DEFINITIONS = [
    {
        "name": "notion_query",
        "description": (
            "Liest Einträge aus einer Notion-Datenbank. "
            "Verfügbare Datenbanken: 'todos', 'projekte', 'konzepte'. "
            "Gibt eine Liste von Einträgen zurück."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte", "konzepte"],
                    "description": "Name der Datenbank",
                },
                "search": {
                    "type": "string",
                    "description": "Suche im Titel (optional)",
                },
                "status": {
                    "type": "string",
                    "description": "Filtert nach Status-Wert (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl Ergebnisse (Standard: 10)",
                },
            },
            "required": ["database"],
        },
    },
    {
        "name": "notion_write",
        "description": (
            "Erstellt einen neuen Eintrag in einer Notion-Datenbank. "
            "Verfügbare Datenbanken: 'todos', 'projekte', 'konzepte'. "
            "todos: Name (Pflicht), Status, Datum (YYYY-MM-DD), Priorität (Niedrig/Mittel/Hoch), Bereich, Aufwand. "
            "projekte: Projekt (Pflicht), Status, Beschreibung, Typ. "
            "konzepte: Name (Pflicht), Status, Notiz, Typ."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte", "konzepte"],
                    "description": "Name der Datenbank",
                },
                "properties": {
                    "type": "object",
                    "description": "Felder des neuen Eintrags als Key-Value-Paare",
                },
            },
            "required": ["database", "properties"],
        },
    },
    {
        "name": "notion_update",
        "description": (
            "Aktualisiert einen bestehenden Notion-Eintrag per page_id. "
            "page_id aus einem vorherigen notion_query entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "ID der Notion-Seite",
                },
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte", "konzepte"],
                    "description": "Name der Datenbank (für Schema-Lookup)",
                },
                "properties": {
                    "type": "object",
                    "description": "Zu ändernde Felder als Key-Value-Paare",
                },
            },
            "required": ["page_id", "database", "properties"],
        },
    },
    {
        "name": "brain_read",
        "description": (
            "Liest einen Wert aus JARVIS's eigenem Gedächtnis (GitHub Brain). "
            "Sections: 'profile' (Simons Profil), 'settings' (aktive Routinen & Präferenzen), "
            "'memory' (was JARVIS über Simon gelernt hat). "
            "key optional – ohne key wird die ganze Section zurückgegeben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "settings", "memory"],
                    "description": "Welche Section lesen",
                },
                "key": {
                    "type": "string",
                    "description": "Optionaler Key innerhalb der Section",
                },
            },
            "required": ["section"],
        },
    },
    {
        "name": "brain_write",
        "description": (
            "Schreibt einen Wert in JARVIS's Gedächtnis (GitHub Brain) und committet automatisch. "
            "Verwenden wenn Simon sagt 'merk dir X', 'vergiss Y', 'von jetzt an Z'. "
            "Sections: 'profile', 'settings', 'memory'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "settings", "memory"],
                    "description": "Welche Section updaten",
                },
                "key": {
                    "type": "string",
                    "description": "Key der gesetzt werden soll",
                },
                "value": {
                    "description": "Wert (String, Zahl oder Boolean)",
                },
            },
            "required": ["section", "key", "value"],
        },
    },
    {
        "name": "notion_delete",
        "description": (
            "Archiviert (löscht) einen Notion-Eintrag per page_id. "
            "page_id aus einem vorherigen notion_query entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "ID der Notion-Seite",
                },
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte", "konzepte"],
                    "description": "Name der Datenbank (für Cache-Invalidierung)",
                },
            },
            "required": ["page_id"],
        },
    },
]


def execute(tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "notion_query":
            results = notion_service.query(
                database=tool_input["database"],
                search=tool_input.get("search"),
                status=tool_input.get("status"),
                limit=tool_input.get("limit", 10),
            )
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "notion_write":
            page_id = notion_service.write(
                database=tool_input["database"],
                properties=tool_input["properties"],
            )
            return f"Erstellt (page_id: {page_id})"

        if tool_name == "notion_update":
            notion_service.update(
                page_id=tool_input["page_id"],
                database=tool_input["database"],
                properties=tool_input["properties"],
            )
            return "Aktualisiert."

        if tool_name == "brain_read":
            result = brain.read(
                section=tool_input["section"],
                key=tool_input.get("key"),
            )
            return json.dumps(result, ensure_ascii=False)

        if tool_name == "brain_write":
            return brain.write(
                section=tool_input["section"],
                key=tool_input["key"],
                value=tool_input["value"],
            )

        if tool_name == "notion_delete":
            notion_service.delete(
                page_id=tool_input["page_id"],
                database=tool_input.get("database"),
            )
            return "Archiviert."

        return f"Unbekanntes Tool: {tool_name}"
    except Exception as e:
        return f"Fehler bei {tool_name}: {e}"
