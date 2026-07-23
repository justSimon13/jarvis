"""D35-Ticket-Integration: holt GitHub Issues aus dem privaten Firmenrepo und
synct sie als Todos (source='github') in local_data.db. Nur Metadaten (Titel,
Beschreibung, Priorität) — niemals Code/Diffs, die bleiben lokal auf Simons Mac
(siehe services/local_exec.py, Phase 3 der D35-Planung)."""
import json
import urllib.error
import urllib.request

import config
import local_data

# D35s echtes Label-Schema für Priorität — mit Simon abgeglichen, nicht geraten.
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


def fetch_issues(state: str = "all") -> list[dict]:
    """Holt alle Issues aus config.D35_GITHUB_REPO. Filtert Pull Requests raus —
    GitHubs /issues-Endpoint liefert die über dasselbe Feld mit ("pull_request"
    im Item), das ist keine Ticket-Quelle."""
    if not config.D35_GITHUB_REPO or not config.D35_GITHUB_TOKEN:
        raise RuntimeError("D35_GITHUB_REPO/D35_GITHUB_TOKEN nicht gesetzt.")

    req = urllib.request.Request(
        f"https://api.github.com/repos/{config.D35_GITHUB_REPO}/issues?state={state}&per_page=100",
        headers={
            "Authorization": f"Bearer {config.D35_GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub-Issues-Abruf fehlgeschlagen ({e.code}): {detail[:300]}") from e
    return [i for i in items if "pull_request" not in i]


def sync_arbeit_tickets() -> dict:
    """Upserted Issues in todos (source='github', external_id=Issue-Nummer).
    Status-Regel EINSEITIG: GitHub closed -> lokal 'Erledigt' erzwingen, aber
    GitHub open darf einen bereits lokal gesetzten Status (z.B. 'In Arbeit')
    NIE zurücksetzen — sonst würde jeder Sync Simons eigenen Fortschritt
    überschreiben."""
    issues = fetch_issues()
    existing = {t["external_id"]: t for t in local_data.list_arbeit_tickets() if t.get("external_id")}

    neu = 0
    aktualisiert = 0
    for issue in issues:
        number = str(issue["number"])
        label_names = [l["name"] for l in issue.get("labels", [])]
        prioritaet = _derive_prioritaet(label_names)
        is_closed = issue.get("state") == "closed"

        fields = {
            "name": issue["title"],
            "prioritaet": prioritaet,
            "repo": config.D35_GITHUB_REPO,
            "body": issue.get("body") or "",
            "labels": json.dumps(label_names, ensure_ascii=False),
        }
        if is_closed:
            fields["status"] = "Erledigt"

        row = existing.get(number)
        if row is None:
            local_data.add_todo(
                name=fields["name"], status=fields.get("status"), prioritaet=prioritaet,
                source="github", external_id=number, repo=config.D35_GITHUB_REPO,
                body=fields["body"], labels=fields["labels"],
            )
            neu += 1
        else:
            local_data.update_todo(row["id"], **fields)
            aktualisiert += 1

    return {"new": neu, "updated": aktualisiert, "total": len(issues)}
