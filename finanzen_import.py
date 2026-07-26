"""
finanzen_import.py — Import von SevDesk-CSV-Exports (Rechnungen/Ausgaben).

Kein API-Zugang (kostet bei SevDesk extra) — Simon exportiert stattdessen manuell
als CSV. Beide Exportformate sind eigenwillig genug für ein eigenes kleines Modul:
- invoices.csv: ein Datensatz pro Zeile, semikolon-getrennt, Header gequotet,
  Datenzeilen NICHT gequotet (csv.reader kommt mit beidem klar).
- voucher.csv: ZWEI Zeilen pro Beleg — eine Kopfzeile (Belegnummer/Status/
  Lieferant/Datum/Betrag, Position-Feld leer) gefolgt von einer oder mehreren
  Positionszeilen (gleiche Belegnummer, Position="1"/"2"/..., Kategorie/
  Beschreibung). Mehrere Positionen werden hier zu einem flachen Datensatz
  zusammengefasst (nur die erste Kategorie/Beschreibung übernommen) — kein
  eigenes Line-Item-Modell, reicht für eine einfache Ausgaben-Liste.

Import ist idempotent (upsert per rechnungsnummer/belegnummer, SevDesks eigene
stabile IDs) — ein erneuter Export/Import derselben Datei erzeugt nie Duplikate,
und eine bereits manuell gesetzte projekt_id an einer Rechnung wird nie
überschrieben (siehe local_data.upsert_rechnung()).

Projekt-Zuordnung bewusst NICHT automatisch geraten: der Kunde (Empfänger-Adresse)
einer Rechnung bestimmt nicht zuverlässig das Projekt — ein Kunde kann mehrere,
unterschiedlich benannte Projekte haben (bestätigt an echten Daten: derselbe
Kunde "Halbautomaten Kommunikationsdesign Gmbh" least sowohl "Ticketsystem" als
auch "knowHere Theme" als auch das separate Projekt "halbautomaten – WordPress
Relaunch"). Import lässt projekt_id deshalb leer — Zuordnung passiert manuell
über die UI oder im Chat mit JARVIS (data_query/data_update auf 'rechnungen').
"""
from __future__ import annotations

import csv
import io
import re

import local_data


def _read_csv_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """BOM entfernen, Header-Namen trimmen (SevDesk hat z.B. 'Position ' mit
    Leerzeichen), Datenzeilen als rohe Listen zurückgeben."""
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def _row_dict(header: list[str], row: list[str]) -> dict[str, str]:
    # zip() kappt automatisch auf die kürzere Länge — SevDesk-Zeilen haben
    # gelegentlich eine Spalte weniger/mehr als der Header (fehlender/
    # zusätzlicher trailing-';'), robuster als ein harter Index-Zugriff.
    return dict(zip(header, row))


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _parse_date(value: str | None) -> str | None:
    """DD.MM.YYYY -> YYYY-MM-DD. Leer (z.B. 'Bezahlt am' bei offenem Posten) -> None."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", value)
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


def _parse_amount(value: str | None) -> float | None:
    """Deutsches Zahlenformat ('1.234,56' oder '21,42', ggf. mit Minus) -> float."""
    value = (value or "").strip()
    if not value:
        return None
    value = value.replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def parse_invoices_csv(text: str) -> list[dict]:
    header, rows = _read_csv_rows(text)
    results = []
    for row in rows:
        raw = _row_dict(header, row)
        rechnungsnummer = _clean(raw.get("Rechnungs-Nr."))
        if not rechnungsnummer:
            continue
        results.append({
            "rechnungsnummer": rechnungsnummer,
            "rechnungsdatum": _parse_date(raw.get("Rechnungs-Datum")),
            "faellig_am": _parse_date(raw.get("Fällig am")),
            "bezahlt_am": _parse_date(raw.get("Bezahlt am")),
            "betreff": _clean(raw.get("Betreff")),
            "betrag_netto": _parse_amount(raw.get("Gesamtbetrag-Netto")),
            "betrag_brutto": _parse_amount(raw.get("Gesamtbetrag-Brutto")),
            "offener_betrag": _parse_amount(raw.get("offener Betrag")),
            "kunde": _clean(raw.get("Empfänger-Adresse")),
        })
    return results


def parse_vouchers_csv(text: str) -> list[dict]:
    header, rows = _read_csv_rows(text)
    vouchers: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        raw = _row_dict(header, row)
        belegnummer = _clean(raw.get("Belegnummer"))
        if not belegnummer:
            continue
        position = _clean(raw.get("Position"))
        if position is None:
            # Kopfzeile eines Belegs
            if belegnummer not in vouchers:
                vouchers[belegnummer] = {
                    "belegnummer": belegnummer,
                    "status": _clean(raw.get("Status")),
                    "lieferant": _clean(raw.get("Lieferant / Kunde")),
                    "datum": _parse_date(raw.get("Datum")),
                    "faellig_am": _parse_date(raw.get("Fällig am")),
                    "bezahlt_am": _parse_date(raw.get("Bezahlt am")),
                    "offener_betrag": _parse_amount(raw.get("offener Betrag")),
                    "betrag": _parse_amount(raw.get("Betrag")),
                    "kategorie": None,
                    "beschreibung": None,
                }
                order.append(belegnummer)
        else:
            # Positionszeile — erste bekannte Kategorie/Beschreibung übernehmen
            v = vouchers.get(belegnummer)
            if v is not None and v["kategorie"] is None:
                v["kategorie"] = _clean(raw.get("Kategorie"))
                v["beschreibung"] = _clean(raw.get("Beschreibung"))
    return [vouchers[b] for b in order]


def import_invoices(csv_text: str) -> dict:
    """Importiert/aktualisiert Rechnungen. Gibt {created, updated, total, unmatched}
    zurück — 'unmatched' listet Rechnungen ohne projekt_id (neue UND bereits
    bestehende unverknüpfte), zur direkten Weiterverwendung z.B. im Chat."""
    parsed = parse_invoices_csv(csv_text)
    existing = {r["rechnungsnummer"]: r for r in local_data.list_rechnungen()}
    created = updated = 0
    for entry in parsed:
        nummer = entry["rechnungsnummer"]
        is_new = nummer not in existing
        fields = {k: v for k, v in entry.items() if k != "rechnungsnummer"}
        local_data.upsert_rechnung(nummer, **fields)
        created += is_new
        updated += not is_new
    unmatched = [
        {"rechnungsnummer": r["rechnungsnummer"], "kunde": r["kunde"], "betrag_netto": r["betrag_netto"]}
        for r in local_data.list_rechnungen() if r["projekt_id"] is None
    ]
    return {"created": created, "updated": updated, "total": len(parsed), "unmatched": unmatched}


def import_vouchers(csv_text: str) -> dict:
    parsed = parse_vouchers_csv(csv_text)
    existing_numbers = {r["belegnummer"] for r in local_data.list_ausgaben()}
    created = updated = 0
    for entry in parsed:
        belegnummer = entry["belegnummer"]
        is_new = belegnummer not in existing_numbers
        fields = {k: v for k, v in entry.items() if k != "belegnummer"}
        local_data.upsert_ausgabe(belegnummer, **fields)
        created += is_new
        updated += not is_new
    return {"created": created, "updated": updated, "total": len(parsed)}


def detect_and_import(csv_text: str) -> dict:
    """Erkennt anhand des Headers, ob es sich um einen Rechnungen- oder
    Ausgaben-Export handelt, und importiert entsprechend — für den Chat-Upload-Pfad
    (pipeline.py), wo es (anders als auf der Rechnungen/Ausgaben-Seite) keine
    explizite UI-Auswahl gibt. Gibt zusätzlich 'kind' zurück."""
    header, _ = _read_csv_rows(csv_text)
    if "Rechnungs-Nr." in header:
        return {"kind": "rechnungen", **import_invoices(csv_text)}
    if "Belegnummer" in header:
        return {"kind": "ausgaben", **import_vouchers(csv_text)}
    raise ValueError("CSV-Format nicht erkannt (weder Rechnungen- noch Ausgaben-Export)")
