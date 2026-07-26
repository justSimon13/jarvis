"""
finanzen.py — Finanzen-Domänenlogik: kombiniert lokal_data.projekte (geschätzter
Wert, erwartetes Abschlussdatum) mit tracking.py's realen Gewinn-Logs
(topic="finanzen", key="gewinn"). Bewusst ein eigenes Modul statt inline in
server.py's Dispatch — tracking.py bleibt domänen-neutral (generische
Zeitreihen-Aggregation, wiederverwendbar für jedes Topic), diese Datei ist die
konkrete Finanzen-Komposition darüber. Serverdispatch (server.py) ruft nur noch
compute_overview()/compute_trend() auf.
"""
from __future__ import annotations

from datetime import date

import local_data
import tracking

_ABGESCHLOSSEN = ("Erledigt", "Archiviert")


def compute_overview() -> dict:
    """Geschätzter Gewinn (offene Projekte) + tatsächlicher Gewinn (reale Logs) +
    Gesamtpotenzial (Summe beider)."""
    projekte_mit_schaetzung = [
        {"id": p["id"], "name": p["name"], "status": p.get("status"), "geschaetzter_wert": p["geschaetzter_wert"]}
        for p in local_data.list_projekte()
        if p.get("geschaetzter_wert") and p.get("status") not in _ABGESCHLOSSEN
    ]
    logs = tracking.get_logs("finanzen", key="gewinn", limit=500)
    geschaetzt_gesamt = sum(p["geschaetzter_wert"] for p in projekte_mit_schaetzung)
    tatsaechlich_gesamt = sum(l["value"] for l in logs if l["value"] is not None)
    return {
        "geschaetzt_gesamt": geschaetzt_gesamt,
        "projekte": projekte_mit_schaetzung,
        "tatsaechlich_gesamt": tatsaechlich_gesamt,
        "tatsaechlich_verlauf": list(reversed(logs)),
        "gesamtpotenzial": geschaetzt_gesamt + tatsaechlich_gesamt,
    }


def _add_months(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def compute_trend(months: int = 12, today: date | None = None) -> dict:
    """Monatlicher Gewinn-Trend, Fenster symmetrisch um den aktuellen Monat
    (z.B. months=12 -> 6 Monate zurück + aktueller Monat + 5 Monate voraus).

    Vergangene/aktuelle Monate: reale Summe aus den Gewinn-Logs.
    Zukünftige Monate mit bekanntem Projektabschluss (projekte.erwartetes_
    abschlussdatum, nicht abgeschlossene Projekte): deren geschaetzter_wert als
    'pipeline'.
    Übrige zukünftige Monate: Hochrechnung = Gesamtsumme bisheriger Gewinne /
    Anzahl Monate seit dem ältesten Eintrag (einfacher fortlaufender Schnitt).
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

    actual_by_month = tracking.get_monthly_series("finanzen", "gewinn", month_keys)

    all_logs = tracking.get_logs("finanzen", key="gewinn", limit=1000)
    total_gewinn = sum(l["value"] for l in all_logs if l["value"] is not None)
    if all_logs:
        earliest = min(l["date"] for l in all_logs)
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
