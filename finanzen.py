"""
finanzen.py — Finanzen-Domänenlogik: kombiniert local_data.projekte (geschätzter
Wert, erwartetes Abschlussdatum) mit local_data.rechnungen/ausgaben (aus SevDesk
importiert, siehe finanzen_import.py). Bewusst ein eigenes Modul statt inline in
server.py's Dispatch — tracking.py bleibt domänen-neutral (generische
Zeitreihen-Aggregation, wiederverwendbar für jedes Topic), diese Datei ist die
konkrete Finanzen-Komposition darüber. Serverdispatch (server.py) ruft nur noch
compute_overview()/compute_trend() auf.

Seit 2026-07-27: "tatsächlicher Gewinn" kommt aus echten, bezahlten Rechnungen
minus echten, bezahlten Ausgaben — nicht mehr aus den manuell per log_entry
gepflegten tracking.py-Einträgen (topic="finanzen", key="gewinn"). Die alten
Einträge waren ohnehin nur eine manuelle Abschrift derselben SevDesk-Rechnungen
(Beträge stimmten exakt überein) — mit echtem Rechnungs-/Ausgaben-Import ist das
jetzt die genauere, direktere Quelle (zieht z.B. auch reale Ausgaben ab, was die
alte Log-Methode nie getan hat). tracking.py selbst bleibt unverändert nutzbar
für andere Topics (Sport etc.) und für Ad-hoc-Einträge, die keiner Rechnung
entsprechen — nur diese Datei liest daraus nicht mehr für die Finanzen-Stats.
"""
from __future__ import annotations

from datetime import date

import local_data

_ABGESCHLOSSEN = ("Erledigt", "Archiviert")


def _paid_rechnungen() -> list[dict]:
    return [r for r in local_data.list_rechnungen() if r.get("bezahlt_am") and r.get("betrag_netto") is not None]


def _paid_ausgaben() -> list[dict]:
    return [a for a in local_data.list_ausgaben() if a.get("bezahlt_am") and a.get("betrag") is not None]


def compute_overview() -> dict:
    """Geschätzter Gewinn (offene Projekte) + tatsächlicher Gewinn (bezahlte
    Rechnungen minus bezahlte Ausgaben) + Gesamtpotenzial (Summe beider)."""
    projekte_mit_schaetzung = [
        {"id": p["id"], "name": p["name"], "status": p.get("status"), "geschaetzter_wert": p["geschaetzter_wert"]}
        for p in local_data.list_projekte()
        if p.get("geschaetzter_wert") and p.get("status") not in _ABGESCHLOSSEN
    ]
    geschaetzt_gesamt = sum(p["geschaetzter_wert"] for p in projekte_mit_schaetzung)

    paid_rechnungen = _paid_rechnungen()
    paid_ausgaben = _paid_ausgaben()
    rechnungen_gesamt = sum(r["betrag_netto"] for r in paid_rechnungen)
    ausgaben_gesamt = sum(a["betrag"] for a in paid_ausgaben)
    tatsaechlich_gesamt = rechnungen_gesamt - ausgaben_gesamt

    verlauf = [
        {"id": f"r{r['id']}", "date": r["bezahlt_am"], "value": r["betrag_netto"],
         "notes": r.get("betreff") or r["rechnungsnummer"]}
        for r in paid_rechnungen
    ] + [
        {"id": f"a{a['id']}", "date": a["bezahlt_am"], "value": -a["betrag"],
         "notes": a.get("beschreibung") or a.get("lieferant") or a["belegnummer"]}
        for a in paid_ausgaben
    ]
    verlauf.sort(key=lambda x: x["date"], reverse=True)

    return {
        "geschaetzt_gesamt": geschaetzt_gesamt,
        "projekte": projekte_mit_schaetzung,
        "tatsaechlich_gesamt": tatsaechlich_gesamt,
        "rechnungen_gesamt": rechnungen_gesamt,
        "ausgaben_gesamt": ausgaben_gesamt,
        "tatsaechlich_verlauf": verlauf,
        "gesamtpotenzial": geschaetzt_gesamt + tatsaechlich_gesamt,
    }


def _add_months(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def _monthly_net(paid_rechnungen: list[dict], paid_ausgaben: list[dict], month_keys: list[str]) -> dict[str, float]:
    """Rechnungen (Netto, positiv) minus Ausgaben (positiv, wird abgezogen) pro
    Monat des jeweiligen bezahlt_am, nur für Monate in month_keys."""
    keys = set(month_keys)
    result: dict[str, float] = {}
    for r in paid_rechnungen:
        month_key = r["bezahlt_am"][:7]
        if month_key in keys:
            result[month_key] = result.get(month_key, 0.0) + r["betrag_netto"]
    for a in paid_ausgaben:
        month_key = a["bezahlt_am"][:7]
        if month_key in keys:
            result[month_key] = result.get(month_key, 0.0) - a["betrag"]
    return result


def compute_trend(months: int = 12, today: date | None = None) -> dict:
    """Monatlicher Gewinn-Trend, Fenster symmetrisch um den aktuellen Monat
    (z.B. months=12 -> 6 Monate zurück + aktueller Monat + 5 Monate voraus).

    Vergangene/aktuelle Monate: reale Summe aus bezahlten Rechnungen minus
    bezahlten Ausgaben (gruppiert nach bezahlt_am).
    Zukünftige Monate mit bekanntem Projektabschluss (projekte.erwartetes_
    abschlussdatum, nicht abgeschlossene Projekte): deren geschaetzter_wert als
    'pipeline'.
    Übrige zukünftige Monate: Hochrechnung = bisheriger Gesamtgewinn / Anzahl
    Monate seit dem ältesten bezahlten Beleg (einfacher fortlaufender Schnitt).
    Zusätzlich eine kumulierte laufende Summe über das gesamte Fenster
    (Ist + Pipeline + Hochrechnung), damit der Longterm-Trend sichtbar wird.

    today: nur für Tests (sonst date.today()) — reiner Parameter, keine
    versteckte globale Uhr nötig.
    """
    if months not in (12, 24):
        months = 12
    today = today or date.today()
    current_month = date(today.year, today.month, 1)

    past_count = months // 2
    future_count = months - past_count - 1
    month_starts = [_add_months(current_month, i) for i in range(-past_count, future_count + 1)]
    month_keys = [d.strftime("%Y-%m") for d in month_starts]
    current_key = current_month.strftime("%Y-%m")

    paid_rechnungen = _paid_rechnungen()
    paid_ausgaben = _paid_ausgaben()
    actual_by_month = _monthly_net(paid_rechnungen, paid_ausgaben, month_keys)

    total_gewinn = sum(r["betrag_netto"] for r in paid_rechnungen) - sum(a["betrag"] for a in paid_ausgaben)
    all_dates = [r["bezahlt_am"] for r in paid_rechnungen] + [a["bezahlt_am"] for a in paid_ausgaben]
    if all_dates:
        earliest = min(all_dates)
        months_since_earliest = (today.year - int(earliest[:4])) * 12 + (today.month - int(earliest[5:7])) + 1
    else:
        months_since_earliest = 0
    avg_per_month = (total_gewinn / months_since_earliest) if months_since_earliest > 0 else 0.0

    pipeline_by_month: dict[str, float] = {}
    for p in local_data.list_projekte():
        d = p.get("erwartetes_abschlussdatum")
        if not d or p.get("status") in _ABGESCHLOSSEN:
            continue
        month_key = d[:7]
        pipeline_by_month[month_key] = pipeline_by_month.get(month_key, 0.0) + (p.get("geschaetzter_wert") or 0.0)

    months_out = []
    cumulative = 0.0
    for month_key in month_keys:
        actual = actual_by_month.get(month_key, 0.0)
        pipeline = 0.0
        projected = 0.0
        if month_key > current_key:
            if month_key in pipeline_by_month:
                pipeline = pipeline_by_month[month_key]
            else:
                projected = avg_per_month
        cumulative += actual + pipeline + projected
        months_out.append({
            "month": month_key,
            "actual": actual,
            "pipeline": pipeline,
            "projected": projected,
            "cumulative": cumulative,
            "is_forecast": month_key > current_key,
        })

    return {"months": months_out, "avg_per_month": avg_per_month}
