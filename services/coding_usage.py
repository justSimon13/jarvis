"""
Kostenerfassung für Coding-Arbeit.

Herausgelöst aus services/coding_engine.py (2026-08-04), damit dieses Modul —
der verworfene erste Ansatz mit Claude Agent SDK und Worktrees — entfernt werden
kann, ohne die Kostenanzeige mitzureißen. Die Erfassung selbst hat mit dem
Ausführungsweg nichts zu tun: sie schreibt und liest ausschließlich `tracking`.

Datenlage bleibt unverändert: Topic 'coding_engine', Key 'cost_usd'. Der Name
ist historisch und wird bewusst NICHT umbenannt — sonst wäre der bisherige
Verlauf in der Kosten-Grafik abgeschnitten.
"""
from datetime import date, timedelta

import config
import tracking

_TRACKING_TOPIC = "coding_engine"


def today_spend() -> float:
    """Summe der heute erfassten Kosten."""
    today = date.today().isoformat()
    logs = tracking.get_logs(_TRACKING_TOPIC, key="cost_usd", since_date=today, limit=1000)
    return round(sum((entry["value"] or 0.0) for entry in logs if entry["date"] == today), 4)


def record_spend(cost_usd: float, label: str = "", note: str = "") -> float:
    """Erfasst die Kosten eines Laufs, gibt den neuen Tageswert zurück.

    label/note sind reine Beschriftung (früher Branch und Instruktion) — für die
    Auswertung zählt nur der Wert.
    """
    tracking.add_log(
        _TRACKING_TOPIC, key="cost_usd", value=round(max(cost_usd, 0.0), 4),
        unit="usd", notes=f"{label}: {note[:100]}" if label or note else None,
    )
    return today_spend()


def get_usage_summary(days: int = 14) -> dict:
    """Für die Kosten-Grafik in jarvis-web (data_request 'coding_engine_usage')."""
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    logs = tracking.get_logs(_TRACKING_TOPIC, key="cost_usd", since_date=since, limit=1000)

    daily: dict[str, float] = {}
    for entry in logs:
        tag = entry["date"]
        daily[tag] = round(daily.get(tag, 0.0) + (entry["value"] or 0.0), 4)

    series = [
        {"date": (date.today() - timedelta(days=days - 1 - i)).isoformat(),
         "cost_usd": daily.get((date.today() - timedelta(days=days - 1 - i)).isoformat(), 0.0)}
        for i in range(days)
    ]
    return {
        "daily": series,
        "today_usd": daily.get(date.today().isoformat(), 0.0),
        "task_limit_usd": config.CODING_TASK_BUDGET_USD,
        "daily_limit_usd": config.CODING_DAILY_BUDGET_USD,
    }
