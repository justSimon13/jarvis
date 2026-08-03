"""
Der austauschbare Kopf des Wissens-Imports: normalisieren und schneiden.

Siehe docs-draft/JARVIS-Datenmodell-und-API.md, Abschnitt `imports`. Der Import
läuft in drei Schritten — normalisieren → schneiden → destillieren — und das
Modell kommt erst im dritten vor. Dieses Modul ist der erste und zweite Schritt:
vollständig deterministisch, kostenlos, ohne API-Zugriff und ohne Datenbank.

Zwei Köpfe für Udemy, weil es zwei Ausgangslagen gibt:

  udemy_export      Verzeichnis aus dem Downloader: CONTENTS.txt (Gliederung aus
                    Udemys eigener API) plus eine Datei je Lektion, benannt
                    "<Kapitel>.<Lektion> <Titel>.txt". Die Zuordnung steckt im
                    Dateinamen — nichts zu erraten. Der bevorzugte Weg.

  udemy_transcript  Eine einzelne, zusammengesetzte Datei mit "=== Sektion ==="
                    und "--- Lektion ---" im Text. Notfallpfad für Quellen ohne
                    Begleitdatei. Vorsicht: solche Exporte sind erfahrungsgemäß
                    unzuverlässig — bei einem real geprüften Chrome-Plugin-Export
                    standen alle Sektionsmarker mechanisch im festen Abstand von
                    drei Zeilen vor den ersten zwölf Lektionen, statt an den
                    echten Grenzen. plan() meldet dieses Muster als Warnung.

Quellenspezifisch ist nur dieser vordere Teil. Destillieren, Schreiben,
Verlinken, Fortschritt und Budget sind für alle Quellen gleich und leben
woanders. Ein neuer Kopf (PDF mit Inhaltsverzeichnis, EPUB-Kapitel) ist ein
Eintrag in ADAPTERS, sonst nichts.

Aufruf:
    python3 -m services.import_adapters plan pfad/zum/output-verzeichnis
    python3 -m services.import_adapters plan datei.txt --json
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Zeichen je Token, grobe Faustzahl für deutschen Fließtext. Bewusst konservativ
# (niedriger Wert = höhere geschätzte Tokenzahl = höhere geschätzte Kosten) —
# eine Schätzung, die zu niedrig ausfällt, ist schlimmer als eine zu hohe.
_CHARS_PER_TOKEN = 3.2

# Ab hier ist "ein Abschnitt = ein Aufruf" unbequem und ein Unterschnitt sinnvoll.
# Orientiert an einem gut destillierbaren Einzelaufruf, nicht am Kontextfenster.
_SECTION_CHAR_WARN = 60_000

# ── Normalisieren ─────────────────────────────────────────────────────────────

_TIMESTAMP_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}.*$"
)
_CUE_NUMBER_RE = re.compile(r"^\s*\d+\s*$")
_WEBVTT_RE = re.compile(r"^\s*(WEBVTT|NOTE\b|Kind:|Language:).*$", re.IGNORECASE)
_INLINE_TIME_RE = re.compile(r"^\s*[\[(]\d{1,2}:\d{2}(?::\d{2})?[\])]\s*")


def normalize(text: str) -> tuple[str, dict]:
    """Entfernt Untertitel-Artefakte und rollende Wiederholungen.

    Untertitelformate wiederholen denselben Satz oft über mehrere Cues hinweg,
    damit er beim Lesen stehen bleibt. Für ein Transkript ist das reine
    Verdopplung — und sie wird sonst mitbezahlt.

    Gibt (bereinigter Text, Statistik) zurück. Die Statistik ist der Nachweis,
    wie viel tatsächlich entfernt wurde, statt es zu behaupten.
    """
    original_len = len(text)
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))

    kept: list[str] = []
    removed = {"timestamps": 0, "cue_numbers": 0, "headers": 0, "duplicates": 0}
    previous_content = None

    for line in text.split("\n"):
        if _TIMESTAMP_RE.match(line):
            removed["timestamps"] += 1
            continue
        if _WEBVTT_RE.match(line):
            removed["headers"] += 1
            continue
        # Eine reine Zahl ist nur dann eine Cue-Nummer, wenn Zeitstempel im
        # Dokument überhaupt vorkommen — sonst wäre "2024" in einem normalen
        # Text plötzlich verschwunden.
        if _CUE_NUMBER_RE.match(line) and removed["timestamps"] > 0:
            removed["cue_numbers"] += 1
            continue

        stripped = _INLINE_TIME_RE.sub("", line).strip()

        # Nur unmittelbar aufeinanderfolgende Wiederholungen zusammenfassen — ein
        # Satz, der später erneut fällt, ist echte Wiederholung des Dozenten.
        if stripped and stripped == previous_content:
            removed["duplicates"] += 1
            continue
        if stripped:
            previous_content = stripped
        kept.append(stripped)

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    stats = {
        "chars_before": original_len,
        "chars_after": len(result),
        "removed_lines": removed,
        "reduction_pct": round((1 - len(result) / original_len) * 100, 1) if original_len else 0.0,
    }
    return result, stats


# ── Kopf 1: Downloader-Verzeichnis (bevorzugt) ────────────────────────────────

# CONTENTS.txt: "1. Kapiteltitel" und "1.3 Lektionstitel [8 min, 13.1.2019]"
_CONTENTS_CHAPTER_RE = re.compile(r"^(\d+)\.\s+(.+?)\s*$")
_CONTENTS_LECTURE_RE = re.compile(r"^(\d+)\.(\d+)\s+(.+?)(?:\s*\[[^\]]*\])?\s*$")
# Dateiname: "2.4 Crawling 3 - Die Google Search Console.txt"
_FILENAME_RE = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")
# Kopfzeile in jeder Lektionsdatei: "# 2.4 Crawling 3 - ..." — redundant, raus.
_FILE_HEADING_RE = re.compile(r"^#\s+\d+\.\d+\s+.*$")

CONTENTS_FILENAME = "CONTENTS.txt"


def parse_contents(path: Path) -> dict:
    """Liest CONTENTS.txt — die Gliederung stammt aus Udemys API, nicht aus dem
    Fließtext. Deshalb ist sie Tatsache und muss nicht geraten werden.

    Reihenfolge im Ergebnis ist die der Datei, nicht sortiert: bei zweistelligen
    Nummern wäre eine Namenssortierung falsch ("2.10" vor "2.2").
    """
    chapters: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = line.rstrip()
        if not line.strip():
            continue
        lecture_match = _CONTENTS_LECTURE_RE.match(line)
        if lecture_match:
            chapter_no, lecture_no, title = lecture_match.groups()
            chapter = chapters.setdefault(int(chapter_no), {"title": None, "lectures": {}})
            chapter["lectures"][int(lecture_no)] = title.strip()
            continue
        chapter_match = _CONTENTS_CHAPTER_RE.match(line)
        if chapter_match:
            chapter_no, title = chapter_match.groups()
            chapter = chapters.setdefault(int(chapter_no), {"title": None, "lectures": {}})
            chapter["title"] = title.strip()
    return chapters


def _strip_file_heading(text: str) -> str:
    """Entfernt die vom Downloader vorangestellte '# <Nr> <Titel>'-Zeile. Der
    Titel steht ohnehin strukturiert daneben; im Fließtext wäre er Rauschen."""
    lines = text.split("\n")
    if lines and _FILE_HEADING_RE.match(lines[0].strip()):
        lines = lines[1:]
    return "\n".join(lines).strip()


def split_udemy_export(directory: Path) -> tuple[list[dict], dict]:
    """Baut Sektionen aus CONTENTS.txt und den Lektionsdateien.

    Ein Kapitel wird eine Sektion, eine Lektionsdatei ein Abschnitt darin. Die
    Zuordnung läuft ausschließlich über den Nummernpräfix des Dateinamens —
    kein Titelvergleich, keine Ähnlichkeitssuche, nichts, was danebengreifen
    kann. Abweichungen zwischen Gliederung und Dateibestand werden gemeldet,
    nicht stillschweigend geglättet.
    """
    contents = parse_contents(directory / CONTENTS_FILENAME)

    files: dict[tuple[int, int], Path] = {}
    unmatched_files: list[str] = []
    for file_path in sorted(directory.glob("*.txt")):
        if file_path.name == CONTENTS_FILENAME:
            continue
        match = _FILENAME_RE.match(file_path.stem)
        if not match:
            unmatched_files.append(file_path.name)
            continue
        files[(int(match.group(1)), int(match.group(2)))] = file_path

    sections: list[dict] = []
    norm_totals = {"chars_before": 0, "chars_after": 0,
                   "removed_lines": {"timestamps": 0, "cue_numbers": 0, "headers": 0, "duplicates": 0}}
    missing_files: list[str] = []

    for chapter_no in sorted(contents):
        chapter = contents[chapter_no]
        lectures = []
        for lecture_no in sorted(chapter["lectures"]):
            title = chapter["lectures"][lecture_no]
            file_path = files.pop((chapter_no, lecture_no), None)
            if file_path is None:
                missing_files.append(f"{chapter_no}.{lecture_no} {title}")
                continue
            raw = file_path.read_text(encoding="utf-8", errors="replace")
            text, stats = normalize(_strip_file_heading(raw))
            norm_totals["chars_before"] += stats["chars_before"]
            norm_totals["chars_after"] += stats["chars_after"]
            for key, value in stats["removed_lines"].items():
                norm_totals["removed_lines"][key] += value
            lectures.append({
                "number": f"{chapter_no}.{lecture_no}",
                "title": title,
                "file": file_path.name,
                "text": text,
                "chars": len(text),
            })
        sections.append({
            "index": chapter_no,
            "title": chapter["title"] or f"Kapitel {chapter_no}",
            "lectures": lectures,
            "chars": sum(lecture["chars"] for lecture in lectures),
            "text": "\n\n".join(f"## {lec['title']}\n{lec['text']}" for lec in lectures),
        })

    # Dateien, die in CONTENTS.txt nicht vorkommen — Reste eines früheren Laufs
    # im selben Verzeichnis sind der wahrscheinlichste Grund.
    orphan_files = [p.name for p in files.values()]

    norm_totals["reduction_pct"] = (
        round((1 - norm_totals["chars_after"] / norm_totals["chars_before"]) * 100, 1)
        if norm_totals["chars_before"] else 0.0
    )
    problems = {
        "missing_files": missing_files,
        "orphan_files": sorted(orphan_files),
        "unnamed_files": unmatched_files,
    }
    return sections, {"normalization": norm_totals, "problems": problems}


# ── Kopf 2: einzelne Datei mit Markern (Notfallpfad) ──────────────────────────

# MULTILINE, damit dieselben Ausdrücke zeilenweise (.match beim Schneiden) und
# gegen den ganzen Text (.search bei der Erkennung) funktionieren.
_SECTION_RE = re.compile(r"^[ \t]*={3,}[ \t]*([^=\n].*?)[ \t]*={3,}[ \t]*$", re.MULTILINE)
_LECTURE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*([^-\n].*?)[ \t]*-{3,}[ \t]*$", re.MULTILINE)


def split_udemy_transcript(text: str) -> list[dict]:
    """Zerlegt eine zusammengesetzte Datei an den Markern.

    Lektionen VOR der ersten Sektionsüberschrift landen in einer Sammel-Sektion
    ohne Titel statt verworfen zu werden — im Zweifel lieber sichtbar falsch
    einsortiert als still verschwunden.
    """
    sections: list[dict] = []
    current_section: dict | None = None
    current_lecture: dict | None = None

    def close_lecture():
        nonlocal current_lecture
        if current_lecture is not None:
            current_lecture["text"] = current_lecture["text"].strip()
            current_lecture["chars"] = len(current_lecture["text"])
            current_section["lectures"].append(current_lecture)
            current_lecture = None

    def open_section(title: str | None):
        nonlocal current_section
        if current_section is not None:
            sections.append(current_section)
        current_section = {"index": len(sections) + 1, "title": title,
                           "lectures": [], "orphan": title is None}

    for line in text.split("\n"):
        section_match = _SECTION_RE.match(line)
        if section_match:
            close_lecture()
            open_section(section_match.group(1))
            continue
        lecture_match = _LECTURE_RE.match(line)
        if lecture_match:
            if current_section is None:
                open_section(None)
            close_lecture()
            current_lecture = {"title": lecture_match.group(1), "text": ""}
            continue
        if current_lecture is not None:
            current_lecture["text"] += line + "\n"
        elif current_section is not None:
            current_lecture = {"title": None, "text": line + "\n"}

    close_lecture()
    if current_section is not None:
        sections.append(current_section)

    for section in sections:
        section["chars"] = sum(lecture["chars"] for lecture in section["lectures"])
        section["text"] = "\n\n".join(
            (f"## {lec['title']}\n{lec['text']}" if lec["title"] else lec["text"])
            for lec in section["lectures"]
        )
    return sections


def check_marker_plausibility(text: str) -> list[str]:
    """Prüft, ob die Sektionsmarker überhaupt an echten Grenzen stehen.

    Real beobachteter Fehlerfall: ein Export schrieb die Sektionstitel
    mechanisch je drei Zeilen vor die ersten zwölf Lektionen, statt an die
    tatsächlichen Kapitelgrenzen — die letzte Sektion enthielt dann 87 % des
    Kurses. Ohne diese Prüfung sähe der Plan plausibel aus und die erzeugten
    Dokumente wären falsch beschriftet.
    """
    lines = text.split("\n")
    section_lines = [i for i, l in enumerate(lines) if _SECTION_RE.match(l)]
    lecture_lines = [i for i, l in enumerate(lines) if _LECTURE_RE.match(l)]
    if not section_lines or not lecture_lines:
        return []

    warnings = []
    gaps = []
    for line_no in section_lines:
        following = next((l for l in lecture_lines if l > line_no), None)
        if following is not None:
            gaps.append(following - line_no)
    if len(gaps) >= 3 and len(set(gaps)) == 1:
        warnings.append(
            f"Alle {len(gaps)} Sektionsmarker stehen im identischen Abstand von {gaps[0]} Zeilen "
            "vor der nächsten Lektion — das Muster deutet auf mechanisch eingefügte statt "
            "inhaltlich gesetzte Marker hin. Die Sektionstitel sind als Liste vermutlich "
            "korrekt, ihre Position nicht."
        )
    if len(lecture_lines) > 3 * len(section_lines):
        warnings.append(
            f"{len(lecture_lines)} Lektionen auf nur {len(section_lines)} Sektionen — "
            "die Sektionsmarker enden vermutlich vorzeitig, der Rest fällt in die letzte Sektion."
        )
    return warnings


# ── Registrierung ─────────────────────────────────────────────────────────────

ADAPTERS = {
    "udemy_export": {
        "kind": "directory",
        "label": "Udemy-Downloader-Verzeichnis (CONTENTS.txt + Datei je Lektion)",
        "detect": lambda path: (path / CONTENTS_FILENAME).exists(),
    },
    "udemy_transcript": {
        "kind": "text",
        "label": "Zusammengesetzte Datei (=== Sektion === / --- Lektion ---)",
        "detect": lambda text: bool(_SECTION_RE.search(text)) and bool(_LECTURE_RE.search(text)),
    },
}


def detect(source) -> str | None:
    """Welcher Kopf passt? None = keiner — dann fragt der Import nach, statt zu raten."""
    is_dir = isinstance(source, Path) and source.is_dir()
    for name, adapter in ADAPTERS.items():
        if adapter["kind"] == "directory" and is_dir and adapter["detect"](source):
            return name
        if adapter["kind"] == "text" and not is_dir and adapter["detect"](source):
            return name
    return None


# ── Plan ──────────────────────────────────────────────────────────────────────

def _estimate_cost(input_chars: int, calls: int, model: str = "claude-sonnet-5") -> dict | None:
    """Kostenschätzung aus llm.MODEL_CATALOG — bewusst kein zweites Preisverzeichnis.
    Ist llm.py nicht importierbar, entfällt die Schätzung, statt sie mit
    veralteten Zahlen zu erfinden."""
    try:
        from llm import MODEL_CATALOG
    except Exception:
        return None
    pricing = MODEL_CATALOG.get(model)
    if not pricing:
        return None
    input_tokens = input_chars / _CHARS_PER_TOKEN
    # Ein Destillat ist deutlich kürzer als die Quelle; ein Fünftel ist eine
    # großzügige Annahme für die Ausgabeseite.
    output_tokens = input_tokens * 0.2
    return {
        "model": model,
        "calls": calls,
        "input_tokens_est": int(input_tokens),
        "output_tokens_est": int(output_tokens),
        "usd_est": round(
            input_tokens / 1_000_000 * pricing["input"]
            + output_tokens / 1_000_000 * pricing["output"], 2),
    }


def plan(path: Path, adapter_name: str | None = None, model: str = "claude-sonnet-5") -> dict:
    """Vollständiger Plan für eine Quelle — ohne einen einzigen API-Aufruf.

    Grundlage für die Kostenabschätzung vor dem Start und zugleich die Antwort
    auf "wo waren wir?": weil dieser Schnitt deterministisch ist, lässt sich der
    Plan jederzeit neu erzeugen und gegen die bereits erzeugten Vorschläge
    halten. Deshalb speichert der Import keinen Fortschrittszähler.
    """
    source = path if path.is_dir() else path.read_text(encoding="utf-8", errors="replace")
    adapter_name = adapter_name or detect(path if path.is_dir() else source)

    if adapter_name is None:
        norm_stats = None
        if not path.is_dir():
            _, norm_stats = normalize(source)
        return {
            "source": str(path), "adapter": None, "normalization": norm_stats,
            "problem": "Keine bekannte Struktur erkannt — Kopf fehlt oder Quelle ist unstrukturiert.",
            "sections": [],
        }
    if adapter_name not in ADAPTERS:
        raise ValueError(f"Unbekannter Adapter: {adapter_name} (bekannt: {', '.join(ADAPTERS)})")

    warnings: list[str] = []

    if ADAPTERS[adapter_name]["kind"] == "directory":
        sections, extra = split_udemy_export(path)
        norm_stats = extra["normalization"]
        problems = extra["problems"]
        for missing in problems["missing_files"]:
            warnings.append(f"In der Gliederung, aber keine Datei vorhanden: {missing}")
        for orphan in problems["orphan_files"]:
            warnings.append(f"Datei ohne Eintrag in {CONTENTS_FILENAME} (Rest eines früheren Laufs?): {orphan}")
        for unnamed in problems["unnamed_files"]:
            warnings.append(f"Dateiname ohne Nummernpräfix, nicht zuzuordnen: {unnamed}")
    else:
        text, norm_stats = normalize(source)
        warnings.extend(check_marker_plausibility(text))
        sections = split_udemy_transcript(text)
        orphans = [s for s in sections if s.get("orphan")]
        if orphans:
            warnings.append(
                f"{len(orphans)} Lektion(en) stehen vor der ersten Sektionsüberschrift "
                "und liegen in einer Sammel-Sektion ohne Titel."
            )

    for section in sections:
        if section["chars"] > _SECTION_CHAR_WARN:
            warnings.append(
                f"Abschnitt {section['index']} ({section['title']}) hat {section['chars']} Zeichen — "
                f"über {_SECTION_CHAR_WARN}, braucht einen Unterschnitt."
            )
        if section["chars"] == 0:
            warnings.append(f"Abschnitt {section['index']} ({section['title']}) ist leer.")

    total_chars = sum(s["chars"] for s in sections)
    return {
        "source": str(path),
        "adapter": adapter_name,
        "normalization": norm_stats,
        "section_count": len(sections),
        "lecture_count": sum(len(s["lectures"]) for s in sections),
        "total_chars": total_chars,
        "cost": _estimate_cost(total_chars, len(sections), model),
        "warnings": warnings,
        "sections": [
            {"index": s["index"], "title": s["title"],
             "lectures": len(s["lectures"]), "chars": s["chars"]}
            for s in sections
        ],
    }


def _de(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _print_plan(result: dict) -> None:
    print(f"[import] Quelle:  {result['source']}")
    n = result.get("normalization")
    if n:
        print(f"[import] Bereinigt: {_de(n['chars_before'])} → {_de(n['chars_after'])} Zeichen (−{n['reduction_pct']} %)")
        r = n["removed_lines"]
        if any(r.values()):
            print(f"[import]   entfernt: {r['timestamps']} Zeitstempel, {r['cue_numbers']} Cue-Nummern, "
                  f"{r['headers']} Kopfzeilen, {r['duplicates']} Wiederholungen")

    if not result.get("adapter"):
        print(f"[import] {result['problem']}")
        return

    print(f"[import] Kopf:    {result['adapter']}")
    print(f"[import] Struktur: {result['section_count']} Abschnitte, {result['lecture_count']} Lektionen, "
          f"{_de(result['total_chars'])} Zeichen")
    cost = result.get("cost")
    if cost:
        print(f"[import] Schätzung: {cost['calls']} Aufrufe, ~{_de(cost['input_tokens_est'])} Input-Tokens, "
              f"~${cost['usd_est']} ({cost['model']})")
    else:
        print("[import] Kostenschätzung nicht möglich (llm.py nicht importierbar)")

    print()
    for s in result["sections"]:
        title = (s["title"] or "(ohne Titel)")[:52]
        print(f"  {s['index']:>3}. {title:<52} {s['lectures']:>3} Lekt. {_de(s['chars']):>9} Z.")

    if result["warnings"]:
        print()
        for w in result["warnings"]:
            print(f"[import] Hinweis: {w}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Wissens-Import: Quelle analysieren und schneiden")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="Quelle analysieren, ohne etwas zu schreiben")
    p.add_argument("path", type=Path, help="Verzeichnis (Downloader-Ausgabe) oder einzelne Datei")
    p.add_argument("--adapter", choices=list(ADAPTERS), help="Kopf erzwingen statt erkennen")
    p.add_argument("--model", default="claude-sonnet-5", help="Modell für die Kostenschätzung")
    p.add_argument("--json", action="store_true", help="Maschinenlesbar ausgeben")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"[import] Nicht gefunden: {args.path}", flush=True)
        return 1

    result = plan(args.path, adapter_name=args.adapter, model=args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else "", end="")
    if not args.json:
        _print_plan(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
