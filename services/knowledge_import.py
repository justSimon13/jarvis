"""
Schritt drei des Wissens-Imports: destillieren und in die Wissensdatenbank schreiben.

Die Schritte eins und zwei (normalisieren, schneiden) stehen in
services/import_adapters.py und sind deterministisch. Hier kommt zum ersten Mal
ein Modell ins Spiel — und zwar je Lektion, nicht je Kapitel: ein fokussierter
Eingabeblock liefert ein schärferes Destillat, die Ausgabelänge gerät nie unter
Druck, und ein Abbruch kostet eine Lektion statt eines ganzen Kapitels. Der
Kostenunterschied liegt bei einem Kurs dieser Größe unter einem Dollar.

Ergebnis je Kurs:
    knowledge/<topic>/<kurs-slug>.md              Übersichtsseite mit [[Links]]
    knowledge/<topic>/<kurs-slug>-01-<kapitel>.md ein Dokument je Kapitel,
                                                  ein "## Abschnitt" je Lektion

Fortsetzbar ohne Fortschrittszähler
-----------------------------------
Weil der Schnitt deterministisch ist, lässt sich der Plan jederzeit kostenlos
neu erzeugen. "Wo waren wir?" ist deshalb kein gespeicherter Zustand, sondern
ein Abgleich: Plan neu schneiden, vorhandene Überschriften in den Zieldateien
lesen, fehlende Lektionen abarbeiten. Ein abgebrochener Lauf wird durch
schlichtes erneutes Starten fortgesetzt.

Aufruf (auf dem Server):
    python3 -m services.knowledge_import --source <verzeichnis> --topic seo --dry-run
    python3 -m services.knowledge_import --source <verzeichnis> --topic seo --budget 3.00
"""
import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import knowledge          # noqa: E402
import llm                # noqa: E402
from services import import_adapters  # noqa: E402
from services import import_store     # noqa: E402

# Pause zwischen Aufrufen — kein Burst gegen die API, gleiches Muster wie der
# Startup-Sweep in thread_naming.py.
_THROTTLE_SECONDS = 1.0

# Anfangs auf 1500 gesetzt in der Annahme "eine Lektion ist kurz". An einer
# 26.000-Zeichen-Lektion lief das Destillat mitten im Satz gegen den Deckel —
# bezahlt, geschrieben, unvollständig, ohne Meldung. Der Deckel schützt nur vor
# Ausreißern; die tatsächliche Länge steuert der Prompt ("strecke nicht"), und
# unbenutzte Tokens kosten nichts.
_MAX_TOKENS = 4000

# Endet ein Destillat nicht auf ein Satzende, wurde es mit hoher Wahrschein-
# lichkeit abgeschnitten. Kein Ersatz für stop_reason, aber llm.complete() gibt
# das nicht zurück, und dessen Signatur zu ändern beträfe auch thread_naming.py.
_SENTENCE_END = tuple(".!?:)`\"'*")

_SYSTEM_PROMPT = """Du destillierst Transkripte von Kursvideos zu dauerhaft nutzbaren Notizen.

Regeln:
- Schreibe Fließtext und Stichpunkte auf Deutsch, in Markdown, OHNE Überschrift — die Überschrift setzt der Aufrufer.
- Behalte konkrete Fakten: Werkzeuge, Kennzahlen, Schwellenwerte, Vorgehensweisen, Beispiele, Fachbegriffe mit Erklärung.
- Wirf alles weg, was nur zum gesprochenen Vortrag gehört: Begrüßung, Verabschiedung, Verweise auf andere Videos, Ankündigungen, Füllsätze, Wiederholungen.
- Wirf ebenso alles weg, was über den Kurs selbst handelt statt über das Fachthema: Vorstellung der Dozenten, deren Werdegang, Erfolge, Bewertungen, Teilnehmerzahlen, Plattform, Kursaufbau, Werbung für andere Kurse. Das sind keine Fachfakten, auch wenn Zahlen darin vorkommen.
- Formuliere zeitlos. Nicht "in diesem Video zeige ich", sondern die Sache selbst.
- Erfinde nichts. Steht etwas nicht im Transkript, fehlt es auch in der Notiz.
- Länge richtet sich nach dem Inhalt: eine dünne Lektion ergibt drei Sätze, eine dichte eine halbe Seite. Strecke nicht.
- Enthält die Lektion nichts fachlich Behaltenswertes (reine Begrüßung, Kursorganisation, Ausblick), antworte mit genau: NICHTS
  Das ist ein gültiges Ergebnis, kein Fehler — lieber kein Abschnitt als ein leerer."""

# Antwortet das Modell hiermit, entsteht kein Abschnitt. Gleiches Muster wie
# thread_naming.py, wo "UNCLEAR" bedeutet: lieber nichts als etwas Falsches.
_NOTHING_MARKER = "NICHTS"


def _slug(text: str) -> str:
    """Dateinamenstauglicher Bezeichner. knowledge.py lehnt Segmente mit '/'
    oder '..' ohnehin ab — hier wird zusätzlich alles entfernt, was in einem
    Markdown-Link oder auf einem fremden Dateisystem stören könnte."""
    text = unicodedata.normalize("NFKD", text)
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    text = "".join(replacements.get(c, c) for c in text.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")[:60] or "unbenannt"


def _existing_headings(topic: str, file: str) -> set[str]:
    """Welche Lektionen stehen schon in der Zieldatei? Grundlage der
    Fortsetzbarkeit — gelesen wird die Datei selbst, nicht ein Zählerstand,
    der auseinanderlaufen könnte."""
    existing = knowledge.read(topic, file)
    if not existing:
        return set()
    return {m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", existing, re.MULTILINE)}


def _demote_headings(text: str) -> str:
    """Schiebt alle Überschriften im Destillat auf Ebene 3 oder tiefer.

    Der Prompt verlangt zwar Text ohne Überschrift, das Modell gliedert längere
    Notizen aber trotzdem mit "##". Zwei Folgen, beide unerwünscht: die Ebene
    kollidiert mit der Lektionsüberschrift, die der Aufrufer setzt — und
    _existing_headings() liest genau diese Ebene, um zu erkennen was bereits
    destilliert ist. Eine modellgesetzte Zwischenüberschrift, die zufällig wie
    ein Lektionstitel heißt, würde diese Lektion künftig überspringen lassen.

    Nach dem Verschieben gilt sauber: H1 = Kapitel, H2 = Lektion, H3+ = Gliederung
    innerhalb einer Lektion.
    """
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[level:].lstrip()
            if rest:  # "#" allein ist keine Überschrift
                out.append("#" * max(3, level) + " " + rest)
                continue
        out.append(line)
    return "\n".join(out)


def _report(on_progress, record, ref, heading, spent, done, empty, skipped,
            total, outcome) -> None:
    """Meldet den Stand nach einer Einheit. Fehler im Melden dürfen den Lauf nie
    beenden — eine kaputte Anzeige ist kein Grund, bezahlte Arbeit abzubrechen."""
    if not on_progress:
        return
    try:
        on_progress({
            "import_id": record["id"] if record else None,
            "ref": ref,
            "title": heading,
            "outcome": outcome,          # "ok" | "leer"
            "done": done,                # tatsächlich bezahlte Einheiten
            "written": done - empty,
            "empty": empty,
            "skipped": skipped,
            "total": total,
            "cost_usd": round(spent, 4),
        })
    except Exception as e:
        print(f"[import] Fortschrittsmeldung fehlgeschlagen: {e}", flush=True)


def _distill(title: str, text: str, model: str) -> tuple[str, float]:
    user_text = f"Titel der Lektion: {title}\n\nTranskript:\n{text}"
    result, usage = llm.complete(_SYSTEM_PROMPT, user_text, model=model, max_tokens=_MAX_TOKENS)
    return result, llm.compute_cost(usage, model)


def run(source: Path, topic: str, course_title: str | None = None,
        model: str = "claude-sonnet-5", budget_usd: float = 5.0,
        dry_run: bool = False, limit: int | None = None,
        only_lecture: str | None = None,
        on_progress=None, should_stop=None) -> dict:
    """Destilliert alle noch fehlenden Lektionen in die Wissensdatenbank.

    budget_usd ist eine harte Bremse, keine Anzeige: wird sie überschritten,
    endet der Lauf mit Status 'budget'. Bereits geschriebene Kapitel bleiben
    erhalten, ein erneuter Aufruf setzt fort.

    on_progress: optionales Callable(dict) — wird nach jeder Einheit aufgerufen.
    Bewusst als Parameter statt als Import auf server.py: dieses Modul läuft
    auch als reines Kommandozeilenwerkzeug ohne laufenden Server, und ein
    Reimport wäre zirkulär (gleiches Muster wie broadcast_web_event bei
    JarvisPipeline).

    should_stop: optionales Callable() -> bool, wird vor jeder Einheit geprüft.
    Kooperatives Anhalten statt eines abgebrochenen Threads — ein Lauf, der
    mitten in einem API-Aufruf hart beendet wird, hinterlässt bezahlte, aber
    nicht geschriebene Arbeit.
    """
    plan = import_adapters.plan(source, model=model)
    if not plan.get("adapter"):
        return {"status": "failed", "problem": plan.get("problem")}

    course_title = course_title or source.resolve().name
    course_slug = _slug(course_title)

    # Der Plan liefert nur die Übersicht; für die Texte selbst wird neu
    # geschnitten. Bewusst zwei Aufrufe statt eines gemeinsamen Zwischenzustands
    # — der Schnitt ist kostenlos, ein mitgeschleppter Cache wäre nur eine
    # weitere Stelle, an der etwas veralten kann.
    if import_adapters.ADAPTERS[plan["adapter"]]["kind"] == "directory":
        sections, _ = import_adapters.split_udemy_export(source)
    else:
        text, _ = import_adapters.normalize(source.read_text(encoding="utf-8", errors="replace"))
        sections = import_adapters.split_udemy_transcript(text)

    print(f"[import] Kurs: {course_title} → knowledge/{topic}/", flush=True)
    print(f"[import] {plan['section_count']} Kapitel, {plan['lecture_count']} Lektionen, "
          f"Modell {model}, Budget ${budget_usd:.2f}", flush=True)
    if dry_run:
        print("[import] Probelauf — es wird nichts geschrieben.", flush=True)

    # Import-Zeile anlegen oder fortsetzen. Ein Probelauf bekommt bewusst keine —
    # er schreibt nichts und ist kein Vorgang, den man später wiederfinden will.
    record = None
    skipped_refs: set[str] = set()
    if not dry_run:
        source_key = str(source.resolve())
        record = import_store.find_for_source(source_key, topic)
        if record:
            skipped_refs = set(record["skipped_refs"])
            spent_before = record["cost_usd"] or 0.0
            print(f"[import] Setzt Import #{record['id']} fort "
                  f"(bisher ${spent_before:.2f}, {len(skipped_refs)} ohne Inhalt vermerkt)", flush=True)
        else:
            new_id = import_store.create(
                source_path=source_key, source_type=plan["adapter"], title=course_title,
                topic=topic, model=model, max_budget_usd=budget_usd,
                section_count=plan["section_count"], lecture_count=plan["lecture_count"],
            )
            record = import_store.get(new_id)
            spent_before = 0.0
            print(f"[import] Neuer Import #{new_id}", flush=True)
        import_store.update(record["id"], status="running")

    spent = 0.0
    done = skipped = empty = cut = 0
    total_units = sum(len(s["lectures"]) for s in sections)
    status = "done"
    chapter_files: list[tuple[str, str]] = []
    written_chapters: set[str] = set()

    for section in sections:
        file_stem = f"{course_slug}-{section['index']:02d}-{_slug(section['title'])}"
        chapter_files.append((file_stem, section["title"]))

        already = _existing_headings(topic, file_stem)

        for lecture in section["lectures"]:
            heading = lecture.get("title") or lecture.get("number") or "Ohne Titel"
            if only_lecture and lecture.get("number") != only_lecture:
                continue
            ref = lecture.get("number") or heading
            if heading in already:
                skipped += 1
                continue
            # Bereits als inhaltsleer bewertet — erneut zu destillieren würde
            # dasselbe Ergebnis erzeugen und dasselbe Geld kosten.
            if ref in skipped_refs:
                skipped += 1
                continue
            if not lecture["text"].strip():
                skipped += 1
                continue
            if spent >= budget_usd:
                status = "budget"
                break
            # Kooperatives Anhalten: VOR dem nächsten Aufruf prüfen, nie
            # mittendrin. Ein Lauf, der während eines API-Aufrufs abgebrochen
            # wird, hinterlässt bezahlte, aber nicht geschriebene Arbeit.
            if should_stop and should_stop():
                status = "stopped"
                break

            try:
                distilled, cost = _distill(heading, lecture["text"], model)
            except Exception as e:
                print(f"[import] FEHLER bei {heading}: {e}", flush=True)
                status = "failed"
                break
            spent += cost
            done += 1

            # Der Aufruf ist bezahlt, auch wenn nichts Behaltenswertes drinstand —
            # deshalb zählt er als erledigt, erzeugt aber keinen Abschnitt. Bei
            # einer Fortsetzung wird die Lektion erneut versucht; das ist der
            # Preis dafür, keinen separaten Zustand zu führen, und betrifft nur
            # die wenigen inhaltsleeren Lektionen.
            if distilled.strip().upper().startswith(_NOTHING_MARKER):
                empty += 1
                if record:
                    import_store.add_skipped(record["id"], ref)
                print(f"[import]   {lecture.get('number', ''):>6} {heading[:52]:<52} "
                      f"{lecture['chars']:>7} → nichts Behaltenswertes  ${spent:.3f}", flush=True)
                _report(on_progress, record, ref, heading, spent_before + spent,
                        done, empty, skipped, total_units, "leer")
                time.sleep(_THROTTLE_SECONDS)
                continue

            _report(on_progress, record, ref, heading, spent_before + spent,
                    done, empty, skipped, total_units, "ok")

            truncated = not distilled.rstrip().endswith(_SENTENCE_END)
            if truncated:
                cut += 1
            print(f"[import]   {lecture.get('number', ''):>6} {heading[:52]:<52} "
                  f"{lecture['chars']:>7} → {len(distilled):>5} Z.  ${spent:.3f}"
                  f"{'  ⚠ endet abrupt, vermutlich abgeschnitten' if truncated else ''}", flush=True)

            if dry_run:
                print("\n" + distilled + "\n", flush=True)
                return {"status": "dry-run", "distilled": distilled, "cost_usd": round(spent, 4)}

            # Kapiteldatei erst hier anlegen — beim ERSTEN Abschnitt, der
            # tatsächlich geschrieben wird. Zwei Gründe, beide real aufgetreten:
            #  - knowledge.append_section() schreibt in eine nicht vorhandene
            #    Datei "# heading" statt "## heading". Die erste Lektion wäre
            #    dann der Dokumenttitel und würde bei einer Fortsetzung nicht
            #    wiedererkannt (_existing_headings sucht "##").
            #  - Wird die Datei dagegen vorab angelegt und liefern anschließend
            #    ALLE Lektionen des Kapitels "nichts Behaltenswertes", bleibt
            #    eine leere Hülle in der Wissensdatenbank und im Index stehen.
            if not knowledge.read(topic, file_stem):
                knowledge.write(
                    topic, file_stem,
                    f"# {section['title']}\n\nKapitel {section['index']} aus "
                    f"[[{topic}/{course_slug}|{course_title}]].\n",
                    tags=[topic, "kurs"],
                )
            written_chapters.add(file_stem)
            knowledge.append_section(topic, file_stem, heading, _demote_headings(distilled))
            time.sleep(_THROTTLE_SECONDS)

            if limit and done >= limit:
                status = "limit"
                break
        if status != "done":
            break

    # Übersichtsseite zuletzt: erst jetzt steht fest, welche Kapiteldateien es
    # tatsächlich gibt. Wird bei jedem Lauf neu geschrieben, damit sie nach einer
    # Fortsetzung vollständig ist.
    # Nur Kapitel verlinken, die es wirklich gibt — in diesem Lauf geschrieben
    # oder aus einem früheren vorhanden. Ein Kapitel, dessen Lektionen sämtlich
    # nichts Behaltenswertes enthielten (typisch für "Einleitung"), existiert
    # nicht und darf auch nicht als toter Link im Index auftauchen.
    if not dry_run:
        existing = [
            (stem, title) for stem, title in chapter_files
            if stem in written_chapters or knowledge.read(topic, stem)
        ]
        if existing:
            links = "\n".join(f"- [[{topic}/{stem}|{title}]]" for stem, title in existing)
            knowledge.write(
                topic, course_slug,
                f"# {course_title}\n\nAus einem Kursimport destilliert. "
                f"{len(existing)} Kapitel.\n\n{links}\n",
                tags=[topic, "kurs"],
            )

    # Kosten fortschreiben statt überschreiben: `spent` ist der Verbrauch DIESES
    # Laufs, in der Zeile steht die Summe über alle Läufe des Imports.
    if record:
        # 'paused' statt 'done', wenn der Lauf an einer Grenze endete — der
        # Import ist dann nicht fertig, sondern angehalten. Das ist der
        # Unterschied, den eine spätere Fortschrittsanzeige braucht.
        final_status = {"done": "done", "budget": "paused", "limit": "paused",
                        "stopped": "paused", "failed": "failed"}.get(status, "paused")
        import_store.update(record["id"], status=final_status,
                            cost_usd=round(spent_before + spent, 4))

    print(f"[import] Ende ({status}): {done - empty} destilliert, {empty} ohne Inhalt, "
          f"{skipped} übersprungen, ${spent:.2f} verbraucht", flush=True)
    if cut:
        print(f"[import] {cut} Abschnitt(e) enden abrupt — vermutlich am Token-Deckel "
              f"abgeschnitten. Betroffene Überschriften aus der Zieldatei löschen, dann "
              f"setzt ein erneuter Lauf genau dort an.", flush=True)
    return {"status": status, "distilled": done - empty, "empty": empty,
            "skipped": skipped, "truncated": cut, "cost_usd": round(spent, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Kursmaterial in die Wissensdatenbank destillieren")
    parser.add_argument("--source", type=Path, required=True, help="Verzeichnis oder Datei")
    parser.add_argument("--topic", required=True, help="Ziel-Topic, z.B. 'seo'")
    parser.add_argument("--title", help="Kurstitel (Standard: Name des Quellverzeichnisses)")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--budget", type=float, default=5.0, help="Harte Obergrenze in USD")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur die erste Lektion destillieren und anzeigen, nichts schreiben")
    parser.add_argument("--limit", type=int, help="Nach N Lektionen anhalten (zum Antesten)")
    parser.add_argument("--lecture", metavar="NR",
                        help="Nur diese Lektion, z.B. '2.4' — zum Beurteilen der Qualität "
                             "an einer inhaltsreichen statt der ersten Lektion")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"[import] Nicht gefunden: {args.source}", flush=True)
        return 1

    result = run(args.source, args.topic, course_title=args.title, model=args.model,
                 budget_usd=args.budget, dry_run=args.dry_run, limit=args.limit,
                 only_lecture=args.lecture)
    return 0 if result["status"] in ("done", "dry-run", "limit") else 1


if __name__ == "__main__":
    sys.exit(main())
