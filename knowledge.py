"""
knowledge.py — Persönliche Wissensdatenbank für JARVIS.

Verwaltet ~/.jarvis/knowledge/<topic>/<file>.md
SQLite-Index für schnelles Suchen ohne alle Dateien zu lesen.

Schreib-Regeln (verbindlich):
  - Prose, Pläne, Kontext, Erkenntnisse → write() hier
  - Strukturierte Ziele/Metriken → tracking.py
  - JARVIS-Regeln, Config → brain.db
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

_KNOWLEDGE_DIR = Path.home() / ".jarvis" / "knowledge"
_INDEX_DB      = Path.home() / ".jarvis" / "knowledge_index.db"

# [[topic/file]] oder [[topic/file|Anzeigetext]] — Wiki-Verlinkung im Fließtext.
_LINK_RE = re.compile(r"\[\[([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+)(?:\|([^\]]+))?\]\]")

# Nur einfache, sichere Segmente — kein '/', kein '..', nicht leer.
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _sanitize_segment(value: str, label: str) -> str:
    """Lehnt topic/file-Werte ab, die keine reinen Pfadsegmente sind (kein '/', kein '..')."""
    if not value or not _SEGMENT_RE.match(value):
        raise ValueError(f"Ungültiger {label}: {value!r}")
    return value


# ── SQLite-Index ──────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    _INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_INDEX_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_index (
            path       TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            tags       TEXT DEFAULT '',
            summary    TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_links (
            from_path TEXT NOT NULL,
            to_path   TEXT NOT NULL,
            PRIMARY KEY (from_path, to_path)
        )
    """)
    conn.commit()
    return conn


# ── Frontmatter ───────────────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parst YAML-Frontmatter. Gibt (meta_dict, body) zurück."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    meta: dict = {}
    for line in content[3:end].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, content[end + 3:].strip()


def _build_frontmatter(topic: str, tags: list[str] | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    tags_str = ", ".join(f'"{t}"' for t in (tags or []))
    return f"---\ntopic: {topic}\nupdated: {now}\ntags: [{tags_str}]\n---\n\n"


def _update_updated_date(content: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return re.sub(r"(updated:\s*)[\d-]+", f"updated: {today}", content)


# ── Auto-Zusammenfassung ──────────────────────────────────────────────────────

def _auto_summary(body: str, max_chars: int = 200) -> str:
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
    text = " ".join(lines)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def _update_index(path_rel: str, topic: str, tags: list[str], body: str):
    with _get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_index (path, topic, tags, summary, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (path_rel, topic, ",".join(tags), _auto_summary(body), datetime.now().isoformat()),
        )


# ── Wiki-Verlinkung ───────────────────────────────────────────────────────────

def _extract_links(body: str) -> list[str]:
    """
    Findet alle [[topic/file]]-Referenzen im Text, doppelte entfernt, Reihenfolge erhalten.
    Normalisiert auf 'topic/file.md' — dasselbe Pfadformat wie knowledge_index.path.
    """
    seen: dict[str, None] = {}
    for target, _label in _LINK_RE.findall(body):
        seen.setdefault(f"{target}.md", None)
    return list(seen)


def _update_links(path_rel: str, body: str):
    """Ersetzt alle ausgehenden Links einer Datei durch die aktuell im Text gefundenen."""
    targets = _extract_links(body)
    with _get_db() as conn:
        conn.execute("DELETE FROM knowledge_links WHERE from_path = ?", (path_rel,))
        conn.executemany(
            "INSERT OR IGNORE INTO knowledge_links (from_path, to_path) VALUES (?, ?)",
            [(path_rel, target) for target in targets],
        )


def get_links(topic: str, file: str) -> dict:
    """Ausgehende Links (im Text geschrieben) und Backlinks (immer berechnet, nie gepflegt)."""
    topic = _sanitize_segment(topic, "topic")
    file = _sanitize_segment(file, "file")
    path_rel = f"{topic}/{file}.md"
    with _get_db() as conn:
        outgoing = [r[0] for r in conn.execute(
            "SELECT to_path FROM knowledge_links WHERE from_path = ? ORDER BY to_path", (path_rel,)
        ).fetchall()]
        backlinks = [r[0] for r in conn.execute(
            "SELECT from_path FROM knowledge_links WHERE to_path = ? ORDER BY from_path", (path_rel,)
        ).fetchall()]
    return {"outgoing": outgoing, "backlinks": backlinks}


def move(from_topic: str, from_file: str, to_topic: str, to_file: str, content: str | None = None):
    """
    Verschiebt eine Knowledge-Datei an einen neuen Ort — echtes Verschieben,
    kein Verweis-Stub: Inhalt wird an den neuen Ort geschrieben (neues
    Frontmatter mit to_topic, updated=heute), die Quelldatei wird wirklich
    gelöscht (Datei + Index + Link-Einträge).

    content: optional — überschreibt den Body vor dem Schreiben (z.B. um
    [[...]]-Links im Text auf neue Adressen anzupassen, wenn mehrere Dateien
    gemeinsam verschoben werden). Ohne Angabe wird der bestehende Body 1:1
    übernommen.

    Schreibt fremde Dateien NICHT automatisch um — gibt die Liste der
    Backlinks zurück (wer noch auf die alte Adresse verweist), damit der
    Aufrufer diese gezielt nachziehen kann.
    """
    from_topic = _sanitize_segment(from_topic, "from_topic")
    from_file = _sanitize_segment(from_file, "from_file")
    to_topic = _sanitize_segment(to_topic, "to_topic")
    to_file = _sanitize_segment(to_file, "to_file")

    old_path = f"{from_topic}/{from_file}.md"
    existing = read(from_topic, from_file)
    if not existing:
        raise ValueError(f"Quelle nicht gefunden: {old_path}")

    meta, body = _parse_frontmatter(existing)
    if content is not None:
        _, body = _parse_frontmatter(content) if content.startswith("---") else ({}, content)
    raw_tags = meta.get("tags", "[]").strip("[]")
    tags = [t.strip().strip('"') for t in raw_tags.split(",") if t.strip()]

    referrers = get_links(from_topic, from_file)["backlinks"]

    write(to_topic, to_file, body, tags=tags)

    old_file_path = _KNOWLEDGE_DIR / from_topic / f"{from_file}.md"
    if old_file_path.exists():
        old_file_path.unlink()
    with _get_db() as conn:
        conn.execute("DELETE FROM knowledge_index WHERE path = ?", (old_path,))
        conn.execute("DELETE FROM knowledge_links WHERE from_path = ? OR to_path = ?", (old_path, old_path))
    _generate_summary(from_topic)
    print(f"[knowledge] Verschoben: {old_path} -> {to_topic}/{to_file}.md", flush=True)

    return referrers


def delete(topic: str, file: str) -> list[str]:
    """
    Löscht eine Knowledge-Datei wirklich (Datei + Index + Link-Einträge) — kein
    Verweis-Stub. Für Duplikate/veraltete Seiten, deren Inhalt bereits woanders
    vollständig vorhanden ist. Gibt die Liste der Backlinks zurück (wer noch auf
    die Adresse verweist — muss von Hand nachgezogen werden).
    """
    topic = _sanitize_segment(topic, "topic")
    file = _sanitize_segment(file, "file")
    path_rel = f"{topic}/{file}.md"

    referrers = get_links(topic, file)["backlinks"]

    file_path = _KNOWLEDGE_DIR / topic / f"{file}.md"
    if file_path.exists():
        file_path.unlink()
    with _get_db() as conn:
        conn.execute("DELETE FROM knowledge_index WHERE path = ?", (path_rel,))
        conn.execute("DELETE FROM knowledge_links WHERE from_path = ? OR to_path = ?", (path_rel, path_rel))
    _generate_summary(topic)
    print(f"[knowledge] Gelöscht: {path_rel}", flush=True)

    return referrers


# ── Öffentliche API ───────────────────────────────────────────────────────────

def read(topic: str, file: str) -> str:
    """Liest eine Knowledge-Datei. Gibt leeren String zurück wenn nicht vorhanden."""
    topic = _sanitize_segment(topic, "topic")
    file = _sanitize_segment(file, "file")
    path = _KNOWLEDGE_DIR / topic / f"{file}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write(topic: str, file: str, content: str, tags: list[str] | None = None):
    """
    Schreibt/überschreibt eine Knowledge-Datei und aktualisiert Index + Summary.
    Fügt Frontmatter hinzu wenn nicht vorhanden. Aktualisiert 'updated'-Datum.
    """
    topic = _sanitize_segment(topic, "topic")
    file = _sanitize_segment(file, "file")
    dir_path = _KNOWLEDGE_DIR / topic
    dir_path.mkdir(parents=True, exist_ok=True)

    if not content.startswith("---"):
        content = _build_frontmatter(topic, tags) + content
    else:
        content = _update_updated_date(content)

    path = dir_path / f"{file}.md"
    path.write_text(content, encoding="utf-8")

    meta, body = _parse_frontmatter(content)
    raw_tags = meta.get("tags", "[]").strip("[]")
    resolved_tags = tags or [t.strip().strip('"') for t in raw_tags.split(",") if t.strip()]
    path_rel = f"{topic}/{file}.md"
    _update_index(path_rel, topic, resolved_tags, body)
    _update_links(path_rel, body)

    _generate_summary(topic)
    print(f"[knowledge] Geschrieben: {topic}/{file}.md", flush=True)


def append_section(topic: str, file: str, heading: str, content: str):
    """Hängt einen neuen Abschnitt (## heading) an eine bestehende Datei an."""
    existing = read(topic, file)
    if not existing:
        write(topic, file, f"# {heading}\n\n{content}")
        return
    new_content = existing.rstrip() + f"\n\n## {heading}\n\n{content}"
    write(topic, file, new_content)


def list_available() -> list[dict]:
    """Alle indizierten Knowledge-Dateien mit Metadaten."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT path, topic, tags, summary, updated_at FROM knowledge_index ORDER BY topic, path"
        ).fetchall()
    return [
        {"path": r[0], "topic": r[1], "tags": r[2], "summary": r[3], "updated_at": r[4]}
        for r in rows
    ]


def search(query: str, max_results: int = 5) -> list[dict]:
    """Durchsucht den Index nach relevanten Dateien (Keyword-Match auf Tags + Summary + Pfad)."""
    query_lower = query.lower()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT path, topic, tags, summary FROM knowledge_index"
        ).fetchall()
    results = []
    for path, topic, tags, summary in rows:
        score = sum(1 for word in query_lower.split() if word in f"{path} {tags} {summary}".lower())
        if score > 0:
            results.append({"path": path, "topic": topic, "summary": summary, "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


def read_summary(topic: str) -> str:
    """Liest die _summary.md eines Topics. Generiert sie neu wenn nicht vorhanden."""
    summary_path = _KNOWLEDGE_DIR / topic / "_summary.md"
    if not summary_path.exists():
        _generate_summary(topic)
    return summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""


def _generate_summary(topic: str):
    """Auto-generiert _summary.md aus allen Nicht-Summary-Dateien im Topic-Ordner."""
    topic_dir = _KNOWLEDGE_DIR / topic
    if not topic_dir.exists():
        return
    lines = [f"# {topic.capitalize()} — Übersicht\n"]
    for md_file in sorted(topic_dir.glob("*.md")):
        if md_file.name == "_summary.md":
            continue
        _, body = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
        lines.append(f"**{md_file.stem}:** {_auto_summary(body, 150)}")
    if len(lines) > 1:
        (topic_dir / "_summary.md").write_text(
            _build_frontmatter(topic, ["summary"]) + "\n".join(lines),
            encoding="utf-8",
        )


def rebuild_index():
    """Scannt alle .md-Dateien neu und baut Index + Link-Graph komplett auf."""
    if not _KNOWLEDGE_DIR.exists():
        _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        return
    with _get_db() as conn:
        conn.execute("DELETE FROM knowledge_links")
    for md_file in _KNOWLEDGE_DIR.rglob("*.md"):
        if md_file.name == "_summary.md":
            continue
        rel_path = str(md_file.relative_to(_KNOWLEDGE_DIR))
        topic = rel_path.split("/")[0]
        content = md_file.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(content)
        raw_tags = meta.get("tags", "[]").strip("[]")
        tags = [t.strip().strip('"') for t in raw_tags.split(",") if t.strip()]
        _update_index(rel_path, topic, tags, body)
        _update_links(rel_path, body)
    print("[knowledge] Index neu aufgebaut.", flush=True)
