"""
Persistenz für Wissens-Importe — die `imports`-Tabelle aus
docs-draft/JARVIS-Datenmodell-und-API.md.

Ein Import ist ein eigenständiges Objekt mit Lebensdauer, nicht im Chat —
dieselbe Begründung wie bei `jobs`, nur ohne Branch, Issue und Projektzwang.
Ohne diese Zeile gäbe es keinen Fortschritt in der Oberfläche, keinen
Kostenverlauf und keine Adresse für "Import 3 fortsetzen".

Bewusst in der ZIELFORM angelegt, mit englischen Bezeichnern, neben der heutigen
Struktur statt an sie angepasst — Leitprinzip aus dem Entwurfs-CLAUDE.md: neue
Struktur neben die alte legen, migrieren, alte entfernen. Beim späteren Umbau
auf `documents` muss diese Tabelle deshalb nicht angefasst werden.

Wohnort ist knowledge_index.db: dort stehen bereits Index, Link-Graph und
Vorschläge, also die Nachbarschaft, in der `documents`/`document_suggestions`
entstehen werden.

KEIN Fortschrittszähler
-----------------------
Weil der Schnitt deterministisch ist, wird der Fortschritt aus den geschriebenen
Dokumenten abgeleitet, nicht gespeichert. Die einzige Ausnahme ist `skipped_refs`
— siehe dort.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PATH = Path.home() / ".jarvis" / "knowledge_index.db"

STATUSES = ("planned", "running", "paused", "done", "failed")


def _get_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS imports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path     TEXT NOT NULL,
            source_type     TEXT NOT NULL,
            title           TEXT NOT NULL,
            topic           TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'planned',
            data_scope      TEXT NOT NULL DEFAULT 'own',
            model           TEXT,
            cost_usd        REAL DEFAULT 0.0,
            max_budget_usd  REAL,
            section_count   INTEGER,
            lecture_count   INTEGER,
            -- Lektionen, die das Modell als "nichts Behaltenswertes" bewertet
            -- hat. Sie erzeugen kein Dokument, an dem man sie wiedererkennen
            -- könnte, und würden bei jeder Fortsetzung erneut bezahlt.
            -- ZWISCHENLÖSUNG: im Zielbild ist der Vermerk je Einheit eine Zeile
            -- in document_suggestions (mit import_id). Solange es die Tabelle
            -- nicht gibt, steht die Liste hier als JSON. Beim Umbau ersatzlos
            -- entfernen, nicht migrieren.
            skipped_refs    TEXT DEFAULT '[]',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _row_to_dict(row) -> dict:
    keys = ("id", "source_path", "source_type", "title", "topic", "status", "data_scope",
            "model", "cost_usd", "max_budget_usd", "section_count", "lecture_count",
            "skipped_refs", "created_at", "updated_at")
    data = dict(zip(keys, row))
    try:
        data["skipped_refs"] = json.loads(data["skipped_refs"] or "[]")
    except Exception:
        data["skipped_refs"] = []
    return data


def find_for_source(source_path: str, topic: str) -> dict | None:
    """Der vorhandene Import für dieselbe Quelle und dasselbe Ziel-Topic.

    Bewusst OHNE Status-Bedingung. Eine frühere Fassung suchte nur nach nicht
    abgeschlossenen Importen — dadurch legte jeder Lauf nach einem `done` eine
    neue Zeile an, ohne die Vermerke der vorherigen, und rechnete die inhalts-
    leeren Einheiten erneut ab. Einen fertigen Import erneut zu starten ist aber
    der Normalfall: der Abgleich ist deterministisch, ein Lauf ohne offene
    Einheiten kostet nichts und dient nur dazu, Nachträge einzusammeln.

    Ein Import ist damit an (Quelle, Topic) gebunden, nicht an einen Lauf.
    """
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, source_path, source_type, title, topic, status, data_scope, model, "
            "cost_usd, max_budget_usd, section_count, lecture_count, skipped_refs, "
            "created_at, updated_at FROM imports "
            "WHERE source_path = ? AND topic = ? "
            "ORDER BY id DESC LIMIT 1",
            (source_path, topic),
        ).fetchone()
    return _row_to_dict(row) if row else None


def create(source_path: str, source_type: str, title: str, topic: str,
           data_scope: str = "own", model: str | None = None,
           max_budget_usd: float | None = None,
           section_count: int | None = None, lecture_count: int | None = None) -> int:
    now = datetime.now().isoformat()
    with _get_db() as conn:
        cur = conn.execute(
            "INSERT INTO imports (source_path, source_type, title, topic, status, data_scope, "
            "model, cost_usd, max_budget_usd, section_count, lecture_count, skipped_refs, "
            "created_at, updated_at) VALUES (?,?,?,?,'planned',?,?,0.0,?,?,?,'[]',?,?)",
            (source_path, source_type, title, topic, data_scope, model,
             max_budget_usd, section_count, lecture_count, now, now),
        )
        return cur.lastrowid


def update(import_id: int, **fields) -> None:
    """Feldweises Aktualisieren. cost_usd wird bewusst NICHT aufaddiert, sondern
    gesetzt — der Aufrufer kennt die Gesamtsumme des Laufs und hat sie bereits
    aus dem Vorstand fortgeschrieben. Ein Aufaddieren hier würde bei einem
    wiederholten Aufruf doppelt zählen."""
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"Unbekannter Status: {fields['status']}")
    clause = ", ".join(f"{k} = ?" for k in fields)
    with _get_db() as conn:
        conn.execute(f"UPDATE imports SET {clause} WHERE id = ?", (*fields.values(), import_id))


def add_skipped(import_id: int, ref: str) -> None:
    """Merkt eine Einheit ohne behaltenswerten Inhalt vor. Idempotent — ein
    erneuter Lauf über dieselbe Einheit fügt sie nicht ein zweites Mal ein."""
    with _get_db() as conn:
        row = conn.execute("SELECT skipped_refs FROM imports WHERE id = ?", (import_id,)).fetchone()
        if not row:
            return
        try:
            refs = json.loads(row[0] or "[]")
        except Exception:
            refs = []
        if ref in refs:
            return
        refs.append(ref)
        conn.execute("UPDATE imports SET skipped_refs = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(refs), datetime.now().isoformat(), import_id))


def get(import_id: int) -> dict | None:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, source_path, source_type, title, topic, status, data_scope, model, "
            "cost_usd, max_budget_usd, section_count, lecture_count, skipped_refs, "
            "created_at, updated_at FROM imports WHERE id = ?", (import_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_imports(limit: int = 50) -> list[dict]:
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, source_path, source_type, title, topic, status, data_scope, model, "
            "cost_usd, max_budget_usd, section_count, lecture_count, skipped_refs, "
            "created_at, updated_at FROM imports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]
