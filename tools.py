import json
import notion_service
import brain
import calendar_service
import email_service
import btc
import reminders_service
import search
import weather
import apple_music_service

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
            "Sections: 'profile', 'settings', 'memory'. "
            "Settings sind nested – Dot-Notation verwenden: z.B. key='features.morning_checkin', key='contacts.email_vip'. "
            "Für Pausen flache Keys nutzen: key='checkin_pausiert_bis', value='2026-05-01'. "
            "Email-VIP manuell: section='settings', key='contacts.email_vip', value=[...bestehende Liste + X]. "
            "Memory (section='memory'): value kann String oder Dict sein, wird als neuer Eintrag angehängt. "
            "Vor dem Schreiben von Listen erst brain_read aufrufen um bestehende Einträge nicht zu überschreiben."
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
        "name": "calendar_query",
        "description": "Zeigt Kalendereinträge für die nächsten N Tage aus Google Calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Wie viele Tage voraus (Standard: 1 = heute)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "calendar_write",
        "description": "Erstellt einen neuen Termin in Google Calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titel des Termins"},
                "start_iso": {"type": "string", "description": "Startzeit ISO 8601, z.B. 2026-04-20T14:00:00+02:00"},
                "end_iso": {"type": "string", "description": "Endzeit ISO 8601"},
                "description": {"type": "string", "description": "Optionale Beschreibung"},
            },
            "required": ["title", "start_iso", "end_iso"],
        },
    },
    {
        "name": "calendar_delete",
        "description": "Löscht einen Termin aus Google Calendar per event_id. event_id aus einem vorherigen calendar_query entnehmen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID des Events"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "email_query",
        "description": "Liest E-Mails aus dem Postfach (GMX/IONOS). Gibt Betreff, Absender und Vorschau zurück.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "IMAP-Filter, z.B. 'UNSEEN' (Standard) oder 'ALL'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl E-Mails (Standard: 5)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "email_send",
        "description": (
            "Sendet eine E-Mail. "
            "WICHTIG: Vor dem Aufruf IMMER explizit bei Simon bestätigen lassen: "
            "'Soll ich die Mail an [to] mit Betreff [subject] wirklich senden?' "
            "Nur ausführen wenn Simon explizit 'ja' oder 'senden' sagt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Empfänger-Adresse"},
                "subject": {"type": "string", "description": "Betreff"},
                "body": {"type": "string", "description": "Nachrichtentext"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "sync_email_vip",
        "description": (
            "Synchronisiert die Email-VIP-Liste aus Notion Kontakte (Mehrfachauswahl=Kunde) "
            "in die JARVIS Settings. Aufrufen wenn Simon sagt 'sync VIP-Liste' oder 'aktualisiere Email-Filter'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "btc_price",
        "description": "Aktuellen Bitcoin-Kurs abrufen (€ und $, 24h-Veränderung).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "shopping_add",
        "description": "Fügt einen oder mehrere Artikel zur Einkaufsliste in Apple Reminders hinzu. Synct via iCloud aufs iPhone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste der Artikel",
                },
                "list_name": {
                    "type": "string",
                    "description": "Name der Reminders-Liste (Standard: Einkaufsliste)",
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "shopping_get",
        "description": "Liest die aktuelle Einkaufsliste aus Apple Reminders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "Name der Liste (Standard: Einkaufsliste)"},
            },
            "required": [],
        },
    },
    {
        "name": "shopping_remove",
        "description": "Markiert einen Artikel auf der Einkaufsliste als erledigt (entfernt ihn).",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name des Artikels"},
                "list_name": {"type": "string", "description": "Name der Liste (Standard: Einkaufsliste)"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "web_search",
        "description": "Sucht im Internet nach aktuellen Informationen. Verwenden für Fragen über aktuelle Ereignisse, Fakten, Preise oder alles was nicht im Kontext steht.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchanfrage"},
                "max_results": {"type": "integer", "description": "Anzahl Ergebnisse (Standard: 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Aktuelles Wetter abrufen. Standardmäßig für Simons Standort (Stuttgart), optional für andere Städte.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Stadt (optional, Standard: Stuttgart)"},
            },
            "required": [],
        },
    },
    {
        "name": "music_current",
        "description": "Zeigt den aktuell spielenden Song in Apple Music.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "music_play_pause",
        "description": "Startet oder pausiert Apple Music.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "music_next",
        "description": "Nächster Track in Apple Music.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "music_previous",
        "description": "Vorheriger Track in Apple Music.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "music_volume",
        "description": "Lautstärke von Apple Music setzen (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Lautstärke 0–100"},
            },
            "required": ["level"],
        },
    },
    {
        "name": "music_search",
        "description": (
            "Song oder Artist in der Apple Music Bibliothek suchen. "
            "Gibt bei mehreren Treffern eine Liste mit Titel, Artist und Album zurück. "
            "Dann music_play_track mit dem passenden index aufrufen — z.B. Album-Version bevorzugen wenn Album-Name kein 'Live' oder 'Concert' enthält."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suche nach Song, Artist oder Album"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "music_play_track",
        "description": "Spielt einen bestimmten Track aus einer vorherigen music_search ab. query und index aus dem Suchergebnis übernehmen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dieselbe Suchanfrage wie bei music_search"},
                "index": {"type": "integer", "description": "Index des gewünschten Tracks (aus Suchergebnis)"},
            },
            "required": ["query", "index"],
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

        if tool_name == "calendar_query":
            results = calendar_service.query(days_ahead=tool_input.get("days_ahead", 1))
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "calendar_write":
            return calendar_service.write(
                title=tool_input["title"],
                start_iso=tool_input["start_iso"],
                end_iso=tool_input["end_iso"],
                description=tool_input.get("description", ""),
            )

        if tool_name == "calendar_delete":
            return calendar_service.delete(event_id=tool_input["event_id"])

        if tool_name == "email_query":
            results = email_service.query(
                filter=tool_input.get("filter", "UNSEEN"),
                limit=tool_input.get("limit", 5),
            )
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "email_send":
            return email_service.send(
                to=tool_input["to"],
                subject=tool_input["subject"],
                body=tool_input["body"],
            )

        if tool_name == "sync_email_vip":
            emails = notion_service.sync_vip_emails()
            brain.write(section="settings", key="contacts.email_vip", value=emails)
            return f"{len(emails)} VIP-Emails synchronisiert: {', '.join(emails) if emails else '–'}"

        if tool_name == "btc_price":
            return json.dumps(btc.get_price(), ensure_ascii=False)

        if tool_name == "shopping_add":
            list_name = tool_input.get("list_name", "Einkaufsliste")
            results = [reminders_service.add_item(i, list_name) for i in tool_input["items"]]
            return " ".join(results)

        if tool_name == "shopping_get":
            list_name = tool_input.get("list_name", "Einkaufsliste")
            items = reminders_service.get_items(list_name)
            return json.dumps(items, ensure_ascii=False)

        if tool_name == "shopping_remove":
            list_name = tool_input.get("list_name", "Einkaufsliste")
            return reminders_service.remove_item(tool_input["item"], list_name)

        if tool_name == "web_search":
            results = search.web_search(
                query=tool_input["query"],
                max_results=tool_input.get("max_results", 5),
            )
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "get_weather":
            result = weather.get_weather(city=tool_input.get("city"))
            return json.dumps(result, ensure_ascii=False)

        if tool_name == "music_current":
            return json.dumps(apple_music_service.get_current_track(), ensure_ascii=False)

        if tool_name == "music_play_pause":
            return apple_music_service.play_pause()

        if tool_name == "music_next":
            return apple_music_service.next_track()

        if tool_name == "music_previous":
            return apple_music_service.previous_track()

        if tool_name == "music_volume":
            return apple_music_service.set_volume(tool_input["level"])

        if tool_name == "music_search":
            return apple_music_service.play_search(tool_input["query"])

        if tool_name == "music_play_track":
            return apple_music_service.play_track_index(tool_input["query"], tool_input["index"])

        if tool_name == "notion_delete":
            notion_service.delete(
                page_id=tool_input["page_id"],
                database=tool_input.get("database"),
            )
            return "Archiviert."

        return f"Unbekanntes Tool: {tool_name}"
    except Exception as e:
        return f"Fehler bei {tool_name}: {e}"
