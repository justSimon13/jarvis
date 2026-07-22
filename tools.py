import json
import brain
import knowledge
import tracking
import local_data
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
from services import coding_engine

DEFINITIONS = [
    {
        "name": "delegate_coding_task",
        "description": (
            "Delegiert eine Programmier-Aufgabe an JARVIS' eigene Coding-Engine, die selbstständig "
            "Dateien anlegt/ändert — standardmäßig im j.a.r.v.i.s.-Server-Repo, oder wahlweise in einem "
            "mit create_project angelegten Projekt (Parameter project). NUR verwenden wenn Simon explizit "
            "möchte dass JARVIS selbst Code schreibt/ändert (z.B. 'JARVIS, füg X hinzu', 'entwickle Y'), "
            "NICHT für normale Konversation oder Fragen über Code. Läuft asynchron im Hintergrund in "
            "einem eigenen Git-Branch (nie main) — Ergebnis kommt per Notification, nicht in dieser Antwort. "
            "Mit check_coding_task_status kann der Fortschritt/Status später im Gespräch abgefragt werden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Klare, vollständige Beschreibung der Coding-Aufgabe.",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Name eines zuvor mit create_project angelegten Projekts, in dem JARVIS statt im "
                        "j.a.r.v.i.s.-Server-Repo arbeiten soll. Weglassen = das j.a.r.v.i.s.-Server-Repo selbst."
                    ),
                },
                "high_power": {
                    "type": "boolean",
                    "description": (
                        "true NUR wenn Simon für diesen Task explizit mehr Rechenleistung/Qualität "
                        "verlangt hat (z.B. 'mit mehr Power', 'das ist komplex, nimm das beste Modell'). "
                        "Nutzt ein deutlich teureres Modell. Default false — für normale Aufgaben "
                        "reicht das günstigere Modell."
                    ),
                },
                "auto_mode": {
                    "type": "boolean",
                    "description": (
                        "true NUR wenn Simon für DIESEN Task explizit gesagt hat, dass JARVIS ohne "
                        "Rückfrage/Freigabe durchprogrammieren soll (z.B. 'im Auto-Modus', 'frag nicht "
                        "nochmal nach', 'zieh das einfach durch'). Überspringt für den kompletten Task "
                        "jede Freigabe-Anfrage, auch bei riskanten Aktionen. Default false — normalerweise "
                        "fragt JARVIS bei riskanten Aktionen (Dateien außerhalb des Projekt-Ordners, "
                        "potenziell gefährliche Bash-Befehle, Secret-Zugriffe) im Dashboard nach. Nur setzen "
                        "wenn Simon das ausdrücklich so gesagt hat, nie von dir aus annehmen."
                    ),
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "check_coding_task_status",
        "description": (
            "Prüft den Status des zuletzt gestarteten delegate_coding_task — läuft er noch, "
            "ist er fertig, oder gab es einen Fehler/Abbruch? Nutzen wenn Simon fragt 'ist der "
            "Task fertig?', 'was macht die Coding-Engine gerade?', 'lief das durch?' o.ä. — ohne "
            "dieses Tool hat JARVIS sonst keine Möglichkeit das im Gespräch nachzuschauen. Zeigt "
            "nur den EINEN zuletzt gestarteten Task (aktuell läuft nie mehr als einer gleichzeitig)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sync_project",
        "description": (
            "Zieht sofort den aktuellen main-Stand von GitHub (git pull), OHNE einen Coding-Task zu "
            "starten — schnell und kostenlos, kein LLM-Sub-Aufruf. Rührt den Checkout nur an wenn er "
            "gerade sauber ist und ein reiner Fast-Forward möglich ist, sonst wird ehrlich gemeldet dass "
            "gerade nicht gepullt werden konnte. Nutzen wenn Simon fragt 'kannst du pullen?', 'bist du "
            "aktuell?', 'zieh dir den neuesten Stand' o.ä. — NICHT für 'füge X hinzu' o.ä., das ist "
            "delegate_coding_task. Vor jedem delegate_coding_task passiert das ohnehin schon automatisch, "
            "dieses Tool ist nur für den direkten 'pull jetzt'-Wunsch ohne dass gerade ein Task ansteht."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Name eines mit create_project angelegten Projekts. Weglassen = das j.a.r.v.i.s.-Server-Repo selbst.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "commit_and_push",
        "description": (
            "Committet uncommittete Änderungen DIREKT im Live-Checkout (nicht über einen isolierten "
            "Coding-Task-Worktree) und pusht sie sofort zu GitHub — landet damit OHNE PR-Review direkt "
            "auf dem aktuell ausgecheckten Branch (meist main). Fragt IMMER zuerst eine Freigabe im "
            "Dashboard mit dem vollen Diff an — das ist hier die einzige Kontrollinstanz. Läuft asynchron "
            "im Hintergrund — Ergebnis kommt per Notification, nicht in dieser Antwort. Nutzen wenn Simon "
            "sagt 'push das', 'commit und push', 'schieb das hoch' o.ä. für Änderungen die schon im "
            "Checkout liegen (z.B. von einem vorherigen Coding-Task der direkt im Live-Checkout "
            "geschrieben hat, oder von Simon selbst) — NICHT für 'füge X hinzu' o.ä., das ist "
            "delegate_coding_task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Name eines mit create_project angelegten Projekts. Weglassen = das j.a.r.v.i.s.-Server-Repo selbst.",
                },
                "message": {
                    "type": "string",
                    "description": "Commit-Message (optional, sinnvoller Default falls weggelassen).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Führt einen beliebigen Shell-Befehl auf dem HP-Server aus — für alles, wofür es kein "
            "dediziertes Tool gibt (Logs prüfen, Speicherplatz checken, einen anderen Service neu "
            "starten, ein Paket installieren, etc.). Fragt IMMER zuerst eine Freigabe im Dashboard mit "
            "dem vollen Befehl an, bevor irgendwas ausgeführt wird — das ist hier die einzige "
            "Kontrollinstanz, da sich bei einem beliebigen Befehl keine engere Grenze ziehen lässt. "
            "Läuft asynchron im Hintergrund — Ergebnis (Ausgabe/Exit-Code) kommt per Notification, nicht "
            "in dieser Antwort. WICHTIG: sudo-Befehle funktionieren nur wenn vorher eine passende "
            "NOPASSWD-sudoers-Regel eingerichtet wurde (sonst Timeout, da niemand ein Passwort eingeben "
            "kann) — bei 'permission denied'/Timeout bei sudo-Befehlen Simon genau das erklären. NICHT "
            "nutzen für Dinge mit eigenem Tool: pullen → sync_project, committen+pushen → "
            "commit_and_push, programmieren → delegate_coding_task, Projekt/Repo anlegen → create_project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Der auszuführende Shell-Befehl (vollständig, inkl. Pipes/Flags falls nötig).",
                },
                "cwd": {
                    "type": "string",
                    "description": "Arbeitsverzeichnis (absoluter Pfad). Weglassen = das j.a.r.v.i.s.-Server-Repo.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "create_project",
        "description": (
            "Legt ein komplett neues Projekt an: GitHub-Repository unter Simons Account PLUS lokaler "
            "Checkout auf dem HP-Server, in einem festen, begrenzten Projekte-Ordner (JARVIS kann damit "
            "keine anderen Ordner auf dem Server anfassen). Fragt IMMER zuerst eine Freigabe im Dashboard "
            "an — nach außen sichtbar (bei public) und nicht mit einem einfachen Undo rückgängig zu "
            "machen. Läuft asynchron im Hintergrund — Ergebnis (angelegt/abgelehnt/Fehler) kommt per "
            "Notification, nicht in dieser Antwort. Nutzen wenn Simon ein komplett neues Projekt starten "
            "will (z.B. 'erstell mir Projekt XY'), NICHT für Änderungen an einem bestehenden Projekt — "
            "dafür delegate_coding_task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Projekt-/Repo-Name — nur Buchstaben, Zahlen, Punkt, Unterstrich, Bindestrich.",
                },
                "description": {
                    "type": "string",
                    "description": "Kurzbeschreibung des Projekts (optional).",
                },
                "private": {
                    "type": "boolean",
                    "description": "true = privates Repo (Default), false = öffentlich.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "data_query",
        "description": (
            "Liest Einträge aus Todos oder Projekten. "
            "Verfügbare Datenbanken: 'todos', 'projekte'. "
            "Gibt eine Liste von Einträgen zurück. Manche Einträge haben ein "
            "'unterseiten'-Feld (Liste von {id, titel}) — das sind nur die Titel, "
            "kein Inhalt (Kosten-Rücksicht). Volltext einer Unterseite bei Bedarf "
            "mit read_seite(seite_id) nachladen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte"],
                    "description": "Name der Datenbank",
                },
                "search": {
                    "type": "string",
                    "description": "Suche im Namen (optional)",
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
        "name": "data_write",
        "description": (
            "Erstellt einen neuen Eintrag in Todos oder Projekten. "
            "Verfügbare Datenbanken: 'todos', 'projekte'. "
            "todos: name (Pflicht), status, datum (YYYY-MM-DD), prioritaet (Niedrig/Mittel/Hoch), bereich, aufwand. "
            "projekte: name (Pflicht), status, beschreibung, typ."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte"],
                    "description": "Name der Datenbank",
                },
                "properties": {
                    "type": "object",
                    "description": "Felder des neuen Eintrags als Key-Value-Paare (lokale Feldnamen, siehe oben)",
                },
            },
            "required": ["database", "properties"],
        },
    },
    {
        "name": "data_update",
        "description": (
            "Aktualisiert einen bestehenden Eintrag per id. "
            "id aus einem vorherigen data_query entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "ID des Eintrags",
                },
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte"],
                    "description": "Name der Datenbank",
                },
                "properties": {
                    "type": "object",
                    "description": "Zu ändernde Felder als Key-Value-Paare",
                },
            },
            "required": ["id", "database", "properties"],
        },
    },
    {
        "name": "data_delete",
        "description": (
            "Löscht einen Eintrag aus Todos oder Projekten per id. "
            "id aus einem vorherigen data_query entnehmen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "ID des Eintrags",
                },
                "database": {
                    "type": "string",
                    "enum": ["todos", "projekte"],
                    "description": "Name der Datenbank",
                },
            },
            "required": ["id", "database"],
        },
    },
    {
        "name": "read_seite",
        "description": (
            "Lädt den vollen Inhalt EINER Unterseite eines Todos/Projekts nach — "
            "lazy: data_query liefert nur Titel+id unter 'unterseiten', der Volltext "
            "kommt erst hier. Falls die Seite selbst wieder Unterseiten hat, kommen "
            "die im Ergebnis auch nur als Titel+id — dafür read_seite erneut aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seite_id": {
                    "type": "integer",
                    "description": "id aus dem 'unterseiten'-Feld eines vorherigen data_query oder read_seite",
                },
            },
            "required": ["seite_id"],
        },
    },
    {
        "name": "brain_read",
        "description": (
            "Liest einen Wert aus JARVIS's Gedächtnis. "
            "Sections: 'profile' (Simons Profil, freie Key-Value-Felder), "
            "'behavior' (Verhaltenspräferenzen), "
            "'memory' (was JARVIS über Simon gelernt hat), "
            "'followups' (offene Punkte für nächstes Gespräch), "
            "'events' (Routinen, Features, Check-In-Regeln), "
            "'modules' (Persönlichkeits-Prompt pro Modus), "
            "'config' (technische Einstellungen, Todos/Projekte-Ladeparameter, Kontakte). "
            "key optional – ohne key wird die ganze Section zurückgegeben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "behavior", "memory", "followups", "events", "modules", "config"],
                    "description": "Welche Section lesen",
                },
                "key": {
                    "type": "string",
                    "description": "Optionaler Key innerhalb der Section (Dot-Notation unterstützt)",
                },
            },
            "required": ["section"],
        },
    },
    {
        "name": "brain_write",
        "description": (
            "Schreibt einen Wert in JARVIS's Gedächtnis. "
            "Verwenden wenn Simon sagt 'merk dir X', 'vergiss Y', 'von jetzt an Z'. "
            "Sections und ihre Verwendung: "
            "'profile': Simons persönliche Daten – freie Keys, z.B. key='interessen', key='btc_bestand'. "
            "'behavior': Verhaltenspräferenzen – key='conversation_style', key='reminder_style'. "
            "'memory': Was JARVIS gelernt hat – value String oder Dict, wird als neuer Eintrag angehängt. "
            "'followups': Offene Punkte für nächstes Gespräch. value=String oder {\"text\":\"...\",\"due\":\"YYYY-MM-DD\"}. null zum Löschen. "
            "'events': Routinen und Features. "
            "Routine-Tracking: key='routines.{name}.last_done', value='YYYY-MM-DD'. "
            "Routine verschieben: key='routines.{name}.deferred_until', value='HH:MM'. "
            "Features: key='features.morning_checkin', value=true/false. "
            "Pausen: key='{feature}_pausiert_bis', value='YYYY-MM-DD'. "
            "'modules': Persönlichkeits-Prompt und Dashboard-Konfiguration. "
            "Basis-Identität: key='base.identity'. "
            "Modus-Prompt: key='modes.{modus}.prompt' (modus = assistent|coach|fokus). "
            "Dashboard-Cards: key='modes.{modus}.cards', value=geordnete Liste von Card-IDs. "
            "Verfügbare Card-IDs: 'transcript' (letztes Gespräch), 'btc' (Bitcoin-Kurs), "
            "'todos' (Todos heute), 'calendar' (Kalender heute), "
            "'alarms' (Wecker), 'followups' (offene Punkte), 'clients' (verbundene Geräte). "
            "Reihenfolge im Array = Anzeigereihenfolge. Nicht genannte Cards werden ausgeblendet. "
            "Dashboard-Schnellaktionen: key='modes.{modus}.quick_actions', value=Liste von Action-IDs. "
            "'config': Technisches. "
            "Todos/Projekte-Config: key='todos_projekte.todos.max'. "
            "Kontakte: key='contacts.email_vip', value=[...Liste]. "
            "Schlaf: key='schlaf.stunden', key='schlaf.fallback'. "
            "Proaktiv: key='proaktiv.kalender_minuten', key='proaktiv.email_aktiv'. "
            "Vor dem Schreiben von Listen erst brain_read aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["profile", "behavior", "memory", "followups", "events", "modules", "config"],
                    "description": "Welche Section updaten",
                },
                "key": {
                    "type": "string",
                    "description": "Key der gesetzt werden soll",
                },
                "value": {
                    "description": "Wert (String, Zahl, Boolean oder Dict). Für followups mit Datum: {\"text\": \"...\", \"due\": \"YYYY-MM-DD\"}. null zum Löschen.",
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
            "Synchronisiert die Email-VIP-Liste aus den Kontakten mit Tag 'Kunde' "
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

    # ── Wissensdatenbank ──────────────────────────────────────────────────────
    {
        "name": "read_knowledge",
        "description": (
            "Liest eine Datei aus der persönlichen Wissensdatenbank. "
            "PROAKTIV AUFRUFEN wenn das Gesprächsthema zu einem bekannten Topic passt — "
            "ohne dass Simon explizit darum bittet. "
            "Beispiele: Simon fragt nach Sport/Training → read_knowledge('sport', 'fitnessplan'). "
            "Simon plant eine App → read_knowledge('programmierung', 'security'). "
            "Simon fragt was geplant war → zuerst search_knowledge, dann read_knowledge. "
            "Verfügbare Topics und Dateien via search_knowledge('') ermitteln."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic-Ordner, z.B. 'sport', 'programmierung', 'simon'"},
                "file":  {"type": "string", "description": "Dateiname ohne .md, z.B. 'fitnessplan', '_core'"},
            },
            "required": ["topic", "file"],
        },
    },
    {
        "name": "write_knowledge",
        "description": (
            "Schreibt oder aktualisiert eine Datei in der Wissensdatenbank. "
            "SOFORT AUFRUFEN — ohne explizite Aufforderung — wenn Simon etwas mitteilt das dauerhaft relevant ist. "
            "Konkrete Trigger: Simon nennt einen Plan, eine Entscheidung, eine Erkenntnis, eine Präferenz, eine Routine, "
            "eine Gewohnheit, eine Meinung über eine Person, ein Projekt, ein Ziel (als Prose), eine Erfahrung. "
            "AUCH aufrufen wenn Simon aktiv nach einem Thema fragt oder Interesse zeigt — dann NICHT die allgemeinen Fakten speichern, "
            "sondern das Interesse selbst: z.B. 'Simon interessiert sich für Bitcoin / fragt aktiv nach den Grundlagen' unter finanzen/interessen.md. "
            "Auch aufrufen wenn Simon sagt 'merk dir', 'denk dran', 'ich will', 'ich habe entschieden', 'ab jetzt'. "
            "NUR Prose/Kontext hier — KEINE reinen Zahlenwerte (→ set_goal oder log_entry). "
            "Vorher read_knowledge aufrufen wenn die Datei bereits existieren könnte, dann Inhalt ergänzen statt überschreiben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":   {"type": "string", "description": "Topic-Ordner, z.B. 'sport'"},
                "file":    {"type": "string", "description": "Dateiname ohne .md"},
                "content": {"type": "string", "description": "Vollständiger Markdown-Inhalt der Datei"},
                "tags":    {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags für den Index, z.B. ['training', 'krafttraining']",
                },
            },
            "required": ["topic", "file", "content"],
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Durchsucht den Wissens-Index nach relevanten Dateien. "
            "PROAKTIV aufrufen wenn ein Thema aufkommt und du nicht sicher bist ob Wissen dazu existiert. "
            "Immer aufrufen bevor write_knowledge — prüfen ob die Datei schon existiert. "
            "Gibt Pfad + kurze Zusammenfassung zurück. Dann read_knowledge für den vollen Inhalt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff, z.B. 'fitness', 'security', 'ernährung'"},
            },
            "required": ["query"],
        },
    },

    # ── Tracking ──────────────────────────────────────────────────────────────
    {
        "name": "set_goal",
        "description": (
            "Setzt oder aktualisiert ein strukturiertes Ziel in tracking.db. "
            "NUR für konkrete Zielwerte mit Einheit — KEINE Prose (→ write_knowledge). "
            "Beispiele: Kalorienziel, Gewichtsziel, Trainingshäufigkeit, Monatsbudget. "
            "topic+key ist der eindeutige Schlüssel, z.B. topic='sport', key='kalorien_ziel'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Themenbereich, z.B. 'sport', 'finanzen'"},
                "key":   {"type": "string", "description": "Schlüssel, z.B. 'kalorien_ziel', 'gewicht_ziel'"},
                "value": {"type": "number", "description": "Zielwert"},
                "unit":  {"type": "string", "description": "Einheit, z.B. 'kcal', 'kg', 'x/Woche'"},
                "label": {"type": "string", "description": "Lesbare Beschreibung, z.B. 'Tägliches Kalorienziel'"},
            },
            "required": ["topic", "key", "value"],
        },
    },
    {
        "name": "log_entry",
        "description": (
            "Schreibt einen Log-Eintrag in tracking.db. "
            "SOFORT AUFRUFEN wenn Simon etwas Messbares berichtet — ohne Aufforderung. "
            "Trigger: Training erwähnt, Gewicht genannt, Kalorien/Mahlzeit beschrieben, Schlafdauer, "
            "gelesene Seiten, erledigte Aufgaben, Ausgaben, Einnahmen. "
            "Parallel zur Antwort aufrufen — Simon nicht fragen ob er es geloggt haben will. "
            "Beispiele: 'Training gemacht' → log_entry('sport', 'training', text_value='Pull-Day'). "
            "'Ich wiege heute 82kg' → log_entry('sport', 'gewicht', value=82.0, unit='kg'). "
            "'Hatte heute 2800 kcal' → log_entry('ernaehrung', 'kalorien', value=2800, unit='kcal')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":      {"type": "string", "description": "Themenbereich"},
                "key":        {"type": "string", "description": "Schlüssel, z.B. 'training', 'gewicht', 'kalorien'"},
                "value":      {"type": "number", "description": "Numerischer Wert (optional)"},
                "text_value": {"type": "string", "description": "Textwert, z.B. 'Pull-Day abgeschlossen' (optional)"},
                "unit":       {"type": "string", "description": "Einheit (optional)"},
                "notes":      {"type": "string", "description": "Zusatzinfos (optional)"},
                "date":       {"type": "string", "description": "Datum YYYY-MM-DD (Standard: heute)"},
            },
            "required": ["topic", "key"],
        },
    },
    {
        "name": "get_progress",
        "description": (
            "Zeigt Ziele + letzten Log-Wert + Trend für ein Topic. "
            "PROAKTIV AUFRUFEN wenn Simon über Fortschritt, Statistiken oder Verlauf fragt. "
            "Gibt Übersicht ohne vollständige Logs — für Details get_logs nicht vorhanden, "
            "stattdessen konkrete Zahlen aus get_progress verwenden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Themenbereich, z.B. 'sport'"},
            },
            "required": ["topic"],
        },
    },
]


def execute(tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "delegate_coding_task":
            return coding_engine.start_task(
                tool_input["instruction"],
                high_power=bool(tool_input.get("high_power", False)),
                auto_mode=bool(tool_input.get("auto_mode", False)),
                project=tool_input.get("project"),
            )

        if tool_name == "check_coding_task_status":
            status = coding_engine.get_task_status()
            if not status:
                return "Es wurde noch nie ein Coding-Task gestartet."
            return json.dumps(status, ensure_ascii=False)

        if tool_name == "sync_project":
            return coding_engine.sync_project(tool_input.get("project"))

        if tool_name == "commit_and_push":
            return coding_engine.commit_and_push(tool_input.get("project"), tool_input.get("message"))

        if tool_name == "run_command":
            return coding_engine.run_command(tool_input["command"], tool_input.get("cwd"))

        if tool_name == "create_project":
            return coding_engine.create_project(
                tool_input["name"],
                tool_input.get("description", ""),
                bool(tool_input.get("private", True)),
            )

        if tool_name == "data_query":
            results = local_data.query(
                database=tool_input["database"],
                search=tool_input.get("search"),
                status=tool_input.get("status"),
                limit=tool_input.get("limit", 10),
            )
            return json.dumps(results, ensure_ascii=False)

        if tool_name == "data_write":
            item_id = local_data.write(
                database=tool_input["database"],
                properties=tool_input["properties"],
            )
            return f"Erstellt (id: {item_id})"

        if tool_name == "data_update":
            local_data.update(
                item_id=tool_input["id"],
                database=tool_input["database"],
                properties=tool_input["properties"],
            )
            return "Aktualisiert."

        if tool_name == "data_delete":
            local_data.delete(
                item_id=tool_input["id"],
                database=tool_input["database"],
            )
            return "Gelöscht."

        if tool_name == "read_seite":
            result = local_data.read_seite(tool_input["seite_id"])
            return json.dumps(result, ensure_ascii=False) if result else "Seite nicht gefunden."

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
            emails = local_data.sync_vip_emails()
            brain.write(section="config", key="contacts.email_vip", value=emails)
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

        # ── Wissensdatenbank ──────────────────────────────────────────────────
        if tool_name == "read_knowledge":
            content = knowledge.read(tool_input["topic"], tool_input["file"])
            if not content:
                return f"Keine Datei gefunden: {tool_input['topic']}/{tool_input['file']}.md"
            return content

        if tool_name == "write_knowledge":
            knowledge.write(
                topic=tool_input["topic"],
                file=tool_input["file"],
                content=tool_input["content"],
                tags=tool_input.get("tags"),
            )
            return f"Gespeichert: {tool_input['topic']}/{tool_input['file']}.md"

        if tool_name == "search_knowledge":
            results = knowledge.search(tool_input["query"])
            if not results:
                return "Keine passenden Einträge in der Wissensdatenbank gefunden."
            return json.dumps(results, ensure_ascii=False)

        # ── Tracking ──────────────────────────────────────────────────────────
        if tool_name == "set_goal":
            return tracking.set_goal(
                topic=tool_input["topic"],
                key=tool_input["key"],
                value=tool_input["value"],
                unit=tool_input.get("unit", ""),
                label=tool_input.get("label", ""),
            )

        if tool_name == "log_entry":
            entry_id = tracking.add_log(
                topic=tool_input["topic"],
                key=tool_input["key"],
                value=tool_input.get("value"),
                text_value=tool_input.get("text_value"),
                unit=tool_input.get("unit", ""),
                notes=tool_input.get("notes", ""),
                log_date=tool_input.get("date"),
            )
            return f"Geloggt (ID: {entry_id})"

        if tool_name == "get_progress":
            result = tracking.get_progress(tool_input["topic"])
            return json.dumps(result, ensure_ascii=False)

        return f"Unbekanntes Tool: {tool_name}"
    except Exception as e:
        return f"Fehler bei {tool_name}: {e}"
