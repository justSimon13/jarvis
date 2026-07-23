"""Ticket-Sync: holt offene/geschlossene Issues aus konfigurierten GitHub-Repos
und synct sie als Todos (source='github'). Läuft über den Mac-Client (dessen
bestehenden 'gh'-Login, siehe services/local_exec.py) statt einem eigenen
Server-Token — kein neues, für einen Org-Admin sichtbares Credential nötig.
Nur Metadaten (Titel, Beschreibung, Priorität) — niemals Code/Diffs, die
bleiben lokal auf dem Mac.

Bewusst generisch: kein Bezug auf einen bestimmten Arbeitgeber/ein bestimmtes
Repo im Code — welche Repos verfolgt werden, ist reine Konfiguration
(brain.config.ticket_repos), nicht fest verdrahtet. Mehrere Repos aus
unterschiedlichen Projekten sind möglich, unterschieden über das repo/labels-
Feld am jeweiligen Ticket, nicht über eine eigene Kategorie im System."""
import json

import brain
import local_data
from services import local_exec

# Priorität-Label-Schema ist pro Repo/Team unterschiedlich — hier nur ein
# gängiger Standard-Fall, bei Bedarf anpassen oder pro Repo konfigurierbar machen.
_PRIORITY_LABELS = {
    "priority:high": "Hoch",
    "priority:medium": "Mittel",
    "priority:low": "Niedrig",
}


def _derive_prioritaet(labels: list[str]) -> str | None:
    for label in labels:
        if label in _PRIORITY_LABELS:
            return _PRIORITY_LABELS[label]
    return None


def _configured_repos() -> list[str]:
    """z.B. ["digital35/xyz", "justSimon13/anderes-projekt"] — per Gespräch
    gesetzt (brain_write(section='config', key='ticket_repos', value=[...])),
    nicht fest im Code, da es mehrere/wechselnde Quellen geben kann."""
    repos = brain.read("config", "ticket_repos")
    if isinstance(repos, str):
        repos = [repos]
    return repos or []


def sync_tickets(repos: list[str] | None = None) -> dict:
    """Upserted Issues aus den konfigurierten (oder übergebenen) Repos in todos
    (source='github', external_id=Issue-Nummer). Status-Regel EINSEITIG:
    GitHub closed erzwingt lokal 'Erledigt', aber GitHub open überschreibt NIE
    einen bereits lokal gesetzten Status (z.B. 'In Arbeit') — sonst würde jeder
    Sync Simons eigenen Fortschritt zurücksetzen."""
    target_repos = repos or _configured_repos()
    if not target_repos:
        return {"error": "Kein Repo konfiguriert (brain.config.ticket_repos)."}

    existing = {t["external_id"]: t for t in local_data.list_tickets() if t.get("external_id")}
    neu = 0
    aktualisiert = 0
    fehler = []

    for repo in target_repos:
        result = local_exec.dispatch("gh_issue_list", repo=repo)
        if not result.get("ok"):
            fehler.append(f"{repo}: {result.get('error')}")
            continue

        for issue in (result.get("data") or []):
            number = str(issue["number"])
            label_names = issue.get("labels") or []
            # gh liefert Labels als Liste von {"name": ...}-Objekten.
            if label_names and isinstance(label_names[0], dict):
                label_names = [l["name"] for l in label_names]
            prioritaet = _derive_prioritaet(label_names)
            is_closed = str(issue.get("state", "")).upper() == "CLOSED"

            fields = {
                "name": issue["title"],
                "prioritaet": prioritaet,
                "repo": repo,
                "body": issue.get("body") or "",
                "labels": json.dumps(label_names, ensure_ascii=False),
            }
            if is_closed:
                fields["status"] = "Erledigt"

            row = existing.get(number)
            if row is None:
                local_data.add_todo(
                    name=fields["name"], status=fields.get("status"), prioritaet=prioritaet,
                    source="github", external_id=number, repo=repo,
                    body=fields["body"], labels=fields["labels"],
                )
                neu += 1
            else:
                local_data.update_todo(row["id"], **fields)
                aktualisiert += 1

    out = {"new": neu, "updated": aktualisiert}
    if fehler:
        out["errors"] = fehler
    return out
