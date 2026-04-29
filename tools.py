import json
import brain
from services import notion as notion_service
from services import calendar as calendar_service
from services import email as email_service
from services import btc
from services import reminders as reminders_service
from services import search
from services import weather
from services import apple_music as apple_music_service
from services import timer as timer_service
from services import alarm as alarm_service
from services import client_music as client_music_service

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
            "konzepte: Name (Pflicht), Status, Notiz, Typ. "
            "Optional: content = Liste von Blöcken die als Seiteninhalt angelegt werden (Checkliste etc.)."
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
                "content": {
                    "type": "array",
                    "description": "Optionale Blöcke als Seiteninhalt. Jedes Item: {type: 'to_do'|'paragraph'|'bullet'|'heading', text: '...'}",
                    "items": {"type": "object"},
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
            "'memory' (was JARVIS über Simon gelernt hat), 'followups' (offene Follow-up Punkte), "
            "'context_config' (was beim Start aus Notion geladen wird). "
            "key optional – ohne key wird die ganze Section zurückgegeben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "settings", "memory", "followups", "context_config"],
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
            "Sections: 'profile', 'settings', 'memory', 'followups', 'context_config'. "
            "followups: offene Punkte die beim nächsten Start angesprochen werden sollen. key=kurzer_schlüssel, value=Beschreibung (String) oder {\"text\": \"...\", \"due\": \"YYYY-MM-DD\"} für zeitgesteuertes Erinnern. null zum Löschen. "
            "Routine-Tracking: routines.{name}.last_done = \"YYYY-MM-DD\" nach abgeschlossener Routine. routines.{name}.deferred_until = \"HH:MM\" wenn Simon verschiebt. "
            "Settings sind nested – Dot-Notation verwenden: z.B. key='features.morning_checkin', key='contacts.email_vip'. "
            "Für Pausen flache Keys nutzen: key='checkin_pausiert_bis', value='2026-05-01'. "
            "Email-VIP manuell: section='settings', key='contacts.email_vip', value=[...bestehende Liste + X]. "
            "Memory (section='memory'): value kann String oder Dict sein, wird als neuer Eintrag angehängt. "
            "context_config: steuert was beim Start aus Notion geladen wird. Dot-Notation: "
            "key='projekte.extra_felder', value=['typ'] – Projekttyp im Prompt anzeigen. "
            "key='projekte.status_filter', value=['In Bearbeitung','Planung'] – welche Projekt-Status geladen werden. "
            "key='todos.tage_zurueck', value=14 – Todos der letzten N Tage laden. "
            "key='todos.max', value=30 – maximale Anzahl Todos. "
            "key='konzepte.laden', value=false – Konzepte komplett ausblenden. "
            "Vor dem Schreiben von Listen erst brain_read aufrufen um bestehende Einträge nicht zu überschreiben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "settings", "memory", "followups", "context_config"],
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
        "name": "music_stop",
        "description": "Stoppt Apple Music komplett.",
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
            "Archiviert (löscht) eine beliebige Notion-Seite per page_id – "
            "funktioniert für Datenbank-Einträge und normale Seiten. "
            "page_id aus notion_query oder notion_search_pages entnehmen."
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
                    "description": "Name der Datenbank (optional, nur für Cache-Invalidierung)",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_append_blocks",
        "description": (
            "Fügt Blöcke (Checkboxen, Text, Aufzählungen) als Inhalt an eine bestehende Notion-Seite an. "
            "WICHTIG: Immer ALLE Blöcke in einem einzigen Aufruf schicken — nicht mehrere Aufrufe hintereinander. "
            "Bis zu 100 Blöcke pro Aufruf, bei mehr wird intern automatisch gebatcht. "
            "page_id aus notion_query oder notion_write (gibt page_id zurück) entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "ID der Notion-Seite",
                },
                "blocks": {
                    "type": "array",
                    "description": "Liste von Blöcken. Jedes Item: {type: 'to_do'|'paragraph'|'bullet'|'heading', text: '...'}. Bei to_do optional: checked: true/false",
                    "items": {"type": "object"},
                },
            },
            "required": ["page_id", "blocks"],
        },
    },
    {
        "name": "notion_search_pages",
        "description": (
            "Sucht beliebige Notion-Seiten nach Titel – nicht nur Datenbank-Einträge. "
            "Gibt page_id, Titel und URL zurück. Danach notion_delete oder notion_append_blocks aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suche im Seitentitel",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Ergebnisse (Standard: 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "notion_clear_page",
        "description": (
            "Löscht alle Blöcke (Inhalte) einer Notion-Seite, behält aber die Seite selbst. "
            "Erstellt automatisch ein lokales Backup — mit notion_restore_page wiederherstellbar. "
            "Danach mit notion_append_blocks neu befüllen. "
            "page_id aus notion_query oder notion_search_pages entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "ID der Notion-Seite",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_restore_page",
        "description": (
            "Stellt eine Notion-Seite aus dem letzten lokalen Backup wieder her. "
            "Backup wird automatisch von notion_clear_page erstellt. "
            "Verwenden wenn JARVIS eine Seite versehentlich geleert hat oder ein Rebuild schiefgelaufen ist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "ID der Notion-Seite",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_read_page",
        "description": (
            "Liest den vollständigen Textinhalt einer Notion-Seite. "
            "with_ids=true gibt jede Zeile mit Block-ID zurück ([id:...]) — nötig wenn danach "
            "notion_update_block oder notion_delete_blocks aufgerufen werden soll. "
            "with_ids=false (Standard) für reines Lesen ohne Bearbeitungsabsicht."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "ID der Notion-Seite"},
                "with_ids": {"type": "boolean", "description": "Block-IDs ausgeben (Standard: false)"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "notion_update_block",
        "description": (
            "Ändert den Textinhalt eines einzelnen Blocks. "
            "block_id aus notion_read_page (with_ids=true) entnehmen. "
            "Für kleine Korrekturen — kein Clear/Rebuild der ganzen Seite nötig."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "ID des Blocks"},
                "text": {"type": "string", "description": "Neuer Textinhalt"},
                "block_type": {
                    "type": "string",
                    "description": "Typ: paragraph, heading_1/2/3, bullet, numbered, to_do, quote (Standard: paragraph)",
                },
            },
            "required": ["block_id", "text"],
        },
    },
    {
        "name": "notion_delete_blocks",
        "description": (
            "Löscht einen oder mehrere Blöcke aus einer Notion-Seite. "
            "block_ids aus notion_read_page (with_ids=true) entnehmen. "
            "Für gezieltes Entfernen von Duplikaten oder veralteten Abschnitten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Liste der Block-IDs die gelöscht werden sollen",
                },
            },
            "required": ["block_ids"],
        },
    },
    {
        "name": "alarm_start",
        "description": (
            "Stellt einen JARVIS-Wecker der auf dem Ziel-Client klingelt. "
            "Standard: max_snooze=3, snooze_minutes=9. "
            "target: Name des Clients (z.B. 'schlafzimmer'). Leer = aktiver Client."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "description": "Stunde (0–23)"},
                "minute": {"type": "integer", "description": "Minute (0–59)"},
                "label": {"type": "string", "description": "Bezeichnung, z.B. 'Aufstehen'"},
                "target": {"type": "string", "description": "Client-Name (z.B. 'schlafzimmer'). Leer = aktiver Client."},
                "snooze_minutes": {"type": "integer", "description": "Snooze-Dauer in Minuten (Standard: 9)"},
                "max_snooze": {"type": "integer", "description": "Max. erlaubte Snoozes (Standard: 2)"},
                "song": {"type": "string", "description": "Song/Artist als Weckton via YouTube (z.B. 'Eye of the Tiger'). Leer = Standard-Beep."},
            },
            "required": ["hour", "minute", "label"],
        },
    },
    {
        "name": "alarm_list",
        "description": "Listet alle aktiven JARVIS-Wecker mit Uhrzeit, Label und Alarm-ID.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "alarm_snooze",
        "description": (
            "DEFAULT-Reaktion wenn der Wecker klingelt und Simon sagt 'Wecker aus', 'Stopp', 'Noch kurz', "
            "'5 Minuten' o.ä. — snoozt den Alarm für N Minuten. "
            "IMMER zuerst snoozen, außer Simon sagt explizit 'Wirklich aus', 'Endgültig', 'Ich stehe auf'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Snooze-Dauer in Minuten (Standard: 9)"},
            },
            "required": [],
        },
    },
    {
        "name": "alarm_dismiss",
        "description": (
            "Bricht den Wecker ENDGÜLTIG ab — nur wenn Simon explizit sagt: "
            "'Wirklich aus', 'Endgültig', 'Abbrechen', 'Ich stehe jetzt auf', 'Kein Snooze mehr'. "
            "Bei einfachem 'Wecker aus' → alarm_snooze verwenden!"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "alarm_id": {"type": "string", "description": "ID des Alarms (optional, leer = alle)"},
            },
            "required": [],
        },
    },
    {
        "name": "timer_set",
        "description": "Startet einen Timer der nach X Minuten/Sekunden abläuft. JARVIS spricht eine Erinnerung.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Beschreibung des Timers, z.B. 'Nudelwasser kocht'"},
                "minutes": {"type": "integer", "description": "Minuten (optional)"},
                "seconds": {"type": "integer", "description": "Sekunden (optional, zusätzlich zu Minuten)"},
            },
            "required": ["label"],
        },
    },
    {
        "name": "client_music_play",
        "description": (
            "Spielt einen Song auf dem Satellite-Client ab (mpv + YouTube). "
            "Für Musik im Schlafzimmer, Wohnzimmer etc. "
            "target: Client-Name (z.B. 'schlafzimmer'). Leer = aktiver Client."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "song": {"type": "string", "description": "Song, Artist oder Playlist-Beschreibung"},
                "target": {"type": "string", "description": "Client-Name (optional, leer = aktiver Client)"},
                "volume": {"type": "integer", "description": "Lautstärke 0–100 (Standard: 70)"},
            },
            "required": ["song"],
        },
    },
    {
        "name": "client_music_stop",
        "description": "Stoppt die Musikwiedergabe auf dem Satellite-Client.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Client-Name (optional, leer = aktiver Client)"},
            },
        },
    },
    {
        "name": "timer_list",
        "description": "Listet alle aktiven Timer und Wecker mit verbleibender Zeit.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "timer_cancel",
        "description": "Bricht einen Timer oder Wecker ab.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label des Timers (sucht nach Übereinstimmung)"},
                "id": {"type": "string", "description": "Exakte Timer-ID (optional, falls bekannt)"},
            },
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
                content=tool_input.get("content"),
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

        if tool_name == "music_stop":
            return apple_music_service.stop()

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

        if tool_name == "notion_append_blocks":
            notion_service.append_blocks(
                page_id=tool_input["page_id"],
                blocks=tool_input["blocks"],
            )
            return "Blöcke hinzugefügt."

        if tool_name == "notion_search_pages":
            results = notion_service.search_pages(
                query=tool_input["query"],
                limit=tool_input.get("limit", 5),
            )
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "notion_clear_page":
            n = notion_service.clear_page(page_id=tool_input["page_id"])
            return f"{n} Blöcke gelöscht (Backup erstellt)."

        if tool_name == "notion_restore_page":
            return notion_service.restore_page(page_id=tool_input["page_id"])

        if tool_name == "notion_read_page":
            return notion_service.read_page(
                page_id=tool_input["page_id"],
                with_ids=tool_input.get("with_ids", False),
            )

        if tool_name == "notion_update_block":
            notion_service.update_block(
                block_id=tool_input["block_id"],
                text=tool_input["text"],
                block_type=tool_input.get("block_type", "paragraph"),
            )
            return "Block aktualisiert."

        if tool_name == "notion_delete_blocks":
            n = notion_service.delete_blocks(block_ids=tool_input["block_ids"])
            return f"{n} Block(s) gelöscht."

        if tool_name == "client_music_play":
            client_music_service.play(
                song=tool_input["song"],
                target=tool_input.get("target") or None,
                volume=tool_input.get("volume", 70),
            )
            return f"Spiele '{tool_input['song']}' auf Client."

        if tool_name == "client_music_stop":
            client_music_service.stop(target=tool_input.get("target") or None)
            return "Musik gestoppt."

        if tool_name == "alarm_start":
            alarm_id, fires_at = alarm_service.schedule(
                label=tool_input["label"],
                hour=tool_input["hour"],
                minute=tool_input["minute"],
                target=tool_input.get("target") or None,
                snooze_minutes=tool_input.get("snooze_minutes", 9),
                max_snooze=tool_input.get("max_snooze", 2),
                song=tool_input.get("song") or None,
            )
            return f"Alarm gesetzt: '{tool_input['label']}' um {fires_at} Uhr. (ID: {alarm_id})"

        if tool_name == "alarm_list":
            alarms = alarm_service.list_alarms()
            if not alarms:
                return "Keine aktiven Wecker."
            return json.dumps([{"id": a["alarm_id"], "zeit": a["fires_at"], "label": a["label"]} for a in alarms], ensure_ascii=False)

        if tool_name == "alarm_snooze":
            ok, msg = alarm_service.snooze_alarm(minutes=tool_input.get("minutes", 9))
            return msg

        if tool_name == "alarm_dismiss":
            alarm_service.dismiss(tool_input.get("alarm_id") or None)
            return "Alarm endgültig gestoppt."

        if tool_name == "timer_set":
            total_seconds = (tool_input.get("minutes", 0) * 60) + tool_input.get("seconds", 0)
            if total_seconds <= 0:
                return "Fehler: Dauer muss > 0 sein."
            timer_id = timer_service.set_timer(tool_input["label"], total_seconds)
            mins, secs = divmod(total_seconds, 60)
            duration = f"{mins}m {secs}s" if mins else f"{secs}s"
            return f"Timer gesetzt: '{tool_input['label']}' läuft in {duration} ab. (ID: {timer_id})"

        if tool_name == "timer_list":
            active = timer_service.list_active()
            if not active:
                return "Keine aktiven Timer oder Wecker."
            return json.dumps(active, ensure_ascii=False)

        if tool_name == "timer_cancel":
            if tool_input.get("id"):
                ok = timer_service.cancel(tool_input["id"])
            else:
                ok = timer_service.cancel_by_label(tool_input.get("label", ""))
            return "Timer abgebrochen." if ok else "Kein passender Timer gefunden."

        return f"Unbekanntes Tool: {tool_name}"
    except Exception as e:
        return f"Fehler bei {tool_name}: {e}"
