# J.A.R.V.I.S. — Produktbeschreibung

Was JARVIS können soll, wie, und wo — plus explizit, was JARVIS (bewusst oder noch) nicht macht.
Technische Umsetzung → `ARCHITECTURE.md`/`CODE_REFERENCE.md`. Tool-Referenz → `TOOLS.md`.

---

## Was JARVIS ist

**Kein Chatbot. Ein persönliches Betriebssystem** für Simons Alltag — lokal auf dem HP-Server, mit echtem Gedächtnis, echten Integrationen, vollständig auf Simon zugeschnitten (Ein-Nutzer-System, kein generisches Produkt). Konzeptuell ein Team aus Rollen (Modi): **Assistent** (Organisation/Alltag), **Coach** (Reflexion/Wachstum), **Fokus** (reduziert aufs Wesentliche). Der Anspruch ist proaktiv, nicht reaktiv: JARVIS soll sich melden, bevor Simon fragen muss — nicht nur auf Eingaben warten.

### Designprinzipien (verbindlich, siehe `TECHNICAL_PLAN.md` §1 für die ausführliche Herleitung)

1. Server ist die einzige Wahrheitsquelle — Clients sind I/O-Geräte
2. Push, nicht Pull
3. Gedächtnis first — jede Unterhaltung hinterlässt Spuren
4. Kontext kostet — nur laden was relevant ist (Token-Budget ist endlich)
5. Robustheit over Features
6. Offene Werkzeugkiste — jede Integration ein neues `services/`-Modul, Kern bleibt unberührt
7. JARVIS denkt, nicht nur reagiert — verbindet Punkte die Simon nicht im Kopf hat

---

## Was JARVIS kann — pro Client

JARVIS selbst ist ein einziger Server; was ein Nutzer davon sieht, hängt vom Client ab.

### jarvis-web (Desktop, Tauri-App auf dem Mac) — vollständigstes Interface

- **Chat** — voller Gesprächsverlauf, Session-Liste, Coding-Engine-Status/Freigabe-Dialoge inline, Datei-Anhänge
- **Wissen** — Markdown-Editor für die Wissensdatenbank direkt im Browser, inkl. Wiki-Verlinkung zwischen Dokumenten (`[[topic/file]]`, klickbare Navigation, automatische Backlinks — siehe `ARCHITECTURE.md`)
- **Todos / Projekte / Kontakte** — volle CRUD-Views, direkt (nicht über den Chat-Umweg)
- **Buchhaltung** (Rechnungen / Ausgaben, seit 2026-07-27) — volle CRUD-Views + CSV-Import (SevDesk-Export, kein API-Zugang), Rechnungen mit Projekten verknüpfbar; alternativ auch als Datei-Anhang direkt im Chat hochladbar, läuft dann automatisch durch denselben Import statt in den LLM-Kontext zu gehen
- **Kalender**, **Tracking** (Ziele + Verlaufsgraphen, Finanzen-Übersicht speist sich aus der Buchhaltung)
- Meldet als einziger Client die `local_exec`-Capability — dadurch der einzige Ort, über den die Coding Engine lokale Befehle (z.B. `gh issue list`) ausführen lassen kann, ohne dass der Server-seitige Prozess selbst Zugriff auf Simons Mac oder GitHub-Credentials braucht

### jarvis-dashboard (iPad, PWA) — schlankeres, touch-optimiertes Interface

Check-in, Dashboard (Modus-Umschaltung, Live-Status, Karten für Todos/Kalender/BTC/Wetter), Transkript-Ansicht, Todos (Erstellung läuft hier bewusst über den Chat-Text statt einer strukturierten Action), Kalender, Einstellungen (Verbindungsstatus, verbundene Clients). **Bewusst kein Wissens-Editor** — das bleibt jarvis-web vorbehalten.

### jarvis-satellite (Wohnzimmer/Schlafzimmer, headless Voice-Client)

Wake-Word (openWakeWord) → RMS-VAD-Aufnahme → Server (STT→LLM→TTS) → Wiedergabe, ohne Tastatur/Bildschirm. Läuft auch bei Server-Trennung eigenständig weiter für alles, was physisch lokal sein muss: Alarm-Klingellogik, Bluetooth-Speaker-Reconnect. Unterstützt Interrupt (erneutes Wake-Word während JARVIS spricht bricht die Antwort ab) und kurze Folgefragen ohne erneutes Wake-Word (40s Fenster).

### Sprachsteuerung (kanalübergreifend)

Drei-Layer-Protokoll: reine Datenabfragen ohne LLM (Layer 1), Quick-Action-Templates die durchs LLM laufen (Layer 2), freies Gespräch (Layer 3) — Details `ARCHITECTURE.md`.

---

## Wie JARVIS entscheidet und handelt

- **Proaktiv, ohne dass ein Gespräch läuft:** Kalender-Reminder, VIP-Email-Erkennung, Todo-/Followup-Reminder, nutzerdefinierte Regeln, abendliche Schlafenszeit-Eskalation — alles über einen zentralen `NotificationDispatcher`, rate-limitiert (max. 3 Pushes/Stunde), damit ein Daemon-Fehler nicht zuspammt.
- **Gedächtnis, das altert:** Fakten über Simon (`brain.memory`) verlieren mit der Zeit an Gewicht, außer explizit als dauerhaft markiert — kein unbegrenzt wachsender, gleich gewichteter Datenberg.
- **Wissen vs. Fakten vs. Zahlen sind getrennt:** Prosa/Pläne/Erkenntnisse → Wissensdatenbank; Micro-Facts über Simon → `brain.memory`; strukturierte Zielwerte/Logs → `tracking.db`. JARVIS wählt das selbst, ohne dass Simon das Schema kennen muss.
- **Code-Arbeit ist immer delegiert, nie ad-hoc auf dem Server:** Auf Zuruf ("JARVIS, bau X") startet eine eigene Coding-Engine-Session in einem isolierten Branch/Worktree, nie auf `main`. Risikoreiche Aktionen brauchen eine sichtbare Freigabe (voller Diff/Befehl), außer bei explizit angeforderter `auto_mode`-Ausnahme für eine einzelne Aufgabe.

---

## Was JARVIS NICHT macht

Explizit, nicht als Lücke, sondern als bewusste oder aktuelle Grenze:

- **Kein Smart-Home** — Lichtsteuerung (Hue) ist eine unpriorisierte Idee (`ROADMAP.md`, "Niedrige Priorität"), nichts davon ist gebaut. Heizung, Steckdosen, Kameras: nicht angebunden.
- **Keine native iPhone-App** — nur Web/PWA (`jarvis-web` responsiv, als PWA installierbar). Eine native SwiftUI-App mit echtem APNs-Push/Siri-Integration ist als möglicher Schritt 2 vorgesehen, aber nicht gebaut, solange die PWA reicht.
- **Kein Multi-User-System** — JARVIS ist auf Simon zugeschnitten, kein generisches Produkt für mehrere Nutzer, kein Auth-System über einen einzigen impliziten Owner hinaus.
- **Kein Docker** — bewusste Entscheidung, weil Audio + Docker auf Linux zuverlässig Probleme macht.
- **Coding Engine merged nie eigenständig nach `main`** — jeder `delegate_coding_task`-Branch braucht einen echten, von Simon zu review­enden GitHub-PR. Die einzige Ausnahme (`commit_and_push`, direkt auf den Live-Checkout) läuft trotzdem immer über einen Freigabe-Dialog mit vollem Diff, nie automatisch.
- **Quellcode verlässt beim Ticket-Feature nie den Mac in Richtung Server** — GitHub-Issues werden über Simons eigenen `gh`-CLI-Login lokal abgefragt; der Server sieht nur Ticket-Metadaten (Titel, Labels, Status), nie Diffs oder Repo-Inhalte. Bewusste Datenschutz-/Rechte-am-Werk-Grenze für Arbeits-Tickets.
- **Nur eine Coding-Task gleichzeitig** — projektübergreifend, kein Parallel-Betrieb mehrerer Aufgaben.
- **Kein automatischer Neustart während aktiver Nutzung** — der Auto-Update-Timer pullt zwar sofort, verschiebt den eigentlichen Prozess-Neustart aber bis JARVIS erkennbar idle ist (keine verbundenen Clients, keine laufende Coding-Task).
- **Keine ungeprüfte Web-Suche als Wahrheitsquelle** — Priorität beim Antworten ist Wissensdatenbank vor `brain.memory` vor Internet-Suche (siehe `TECHNICAL_PLAN.md` §3.13); `web_search` ist ein Werkzeug, kein automatisches Vertrauen in Suchergebnisse.
- **Kein RAG/Embedding-basiertes Retrieval (noch nicht)** — Wissens-Suche ist aktuell reiner Keyword-Match auf Pfad/Tags/Auto-Summary, nicht den vollen Dateiinhalt. Für die aktuelle Größe der Wissensdatenbank (~11 Dateien) ausreichend, skaliert aber nicht unbegrenzt (siehe ROADMAP.md, Phase 6).
