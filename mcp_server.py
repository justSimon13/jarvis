#!/usr/bin/env python3
"""
JARVIS MCP Server — Claude Code Integration.

Macht JARVIS's Wissen für Claude Code Sessions zugänglich.
Zwei Scopes: personal (alles) und work (nur Programmierung + Digital35).

─── Lokale Verwendung (stdio) ────────────────────────────────────────────────
Füge zu ~/.claude/settings.json auf dem MacBook hinzu:
{
  "mcpServers": {
    "jarvis": {
      "command": "python3",
      "args": ["/Users/simon/Documents/Arbeit/Simon Fischer Consulting/Apps/j.a.r.v.i.s./mcp_server.py"],
      "env": { "JARVIS_MCP_SCOPE": "personal" }
    }
  }
}

─── Remote via Tailscale (SSE) ───────────────────────────────────────────────
python3 mcp_server.py --transport sse --port 8766

Dann in ~/.claude/settings.json (Arbeits-MacBook):
{
  "mcpServers": {
    "jarvis-work": {
      "url": "http://hp-elitedesk.tail[xxx].ts.net:8766/sse",
      "env": { "JARVIS_MCP_SCOPE": "work" }
    }
  }
}

─── Systemd Service (HP Server, für SSE) ─────────────────────────────────────
/etc/systemd/system/jarvis-mcp.service
  [Unit]
  Description=JARVIS MCP Server
  After=network.target

  [Service]
  User=simon
  WorkingDirectory=/home/simon/j.a.r.v.i.s
  ExecStart=/usr/bin/python3 /home/simon/j.a.r.v.i.s/mcp_server.py --transport sse --port 8766
  Restart=on-failure
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SCOPE = os.getenv("JARVIS_MCP_SCOPE", "personal")
_WORK_TOPICS = {"programmierung", "digital35"}

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:
    print("mcp package fehlt — installiere: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP(
    "jarvis",
    instructions=(
        f"Du hast Zugriff auf JARVIS, Simons persönliches Wissenssystem. Scope: {SCOPE}.\n"
        "Rufe jarvis_get_coding_context() proaktiv am Anfang jeder Session auf.\n"
        + ("Vollzugriff auf: knowledge/, tracking/, brain.memory\n" if SCOPE == "personal"
           else "Work-Scope: nur knowledge/programmierung/ und knowledge/digital35/\n")
        + "Nutze jarvis_write_knowledge() wenn etwas Wichtiges gelernt oder entschieden wurde."
    ),
    # FastMCP aktiviert DNS-Rebinding-Schutz standardmäßig nur für localhost — bei SSE über
    # die LAN-IP (DHCP, ändert sich gelegentlich) würde jede Verbindung mit "Invalid Host
    # header" abgelehnt. Server ist nur im lokalen Netz erreichbar, daher hier bewusst aus.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _check_scope(topic: str) -> bool:
    if SCOPE == "personal":
        return True
    return topic in _WORK_TOPICS


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def jarvis_get_coding_context() -> str:
    """
    Lädt JARVIS-Kontext für die aktuelle Coding-Session.
    Proaktiv am Anfang jeder Session aufrufen.
    Gibt zurück: Tech-Stack, aktive Projekte, Coding-Standards, offene Punkte.
    """
    import brain
    import knowledge as k

    parts: list[str] = []

    if SCOPE == "personal":
        core = k.read("simon", "_core")
        if core:
            relevant_lines = [
                line for line in core.splitlines()
                if any(kw in line.lower() for kw in (
                    "stack", "technolog", "programmier", "freelanc",
                    "projekt", "angular", "vue", "python", "typescript",
                    "digital35", "consulting",
                ))
            ]
            if relevant_lines:
                parts.append("## Simon — Tech-Kontext\n" + "\n".join(relevant_lines))

    prog_summary = k.read_summary("programmierung")
    if prog_summary:
        parts.append(prog_summary)

    d35_summary = k.read_summary("digital35")
    if d35_summary:
        parts.append(d35_summary)

    if SCOPE == "personal":
        try:
            followups = brain.read("followups") or {}
            from datetime import date
            today = date.today().isoformat()
            open_items = []
            for _key, v in followups.items():
                if isinstance(v, dict):
                    if not v.get("due") or v.get("due", "") <= today:
                        open_items.append(v.get("text", _key))
                else:
                    open_items.append(str(v))
            if open_items:
                parts.append("## Offene Punkte\n" + "\n".join(f"- {i}" for i in open_items[:5]))
        except Exception:
            pass

    return "\n\n---\n\n".join(parts) if parts else "Kein Coding-Kontext verfügbar."


@mcp.tool()
def jarvis_search_knowledge(query: str) -> str:
    """
    Durchsucht JARVIS's Wissensdatenbank nach relevanten Dateien.
    Vor jarvis_read_knowledge aufrufen um zu prüfen was existiert.
    Gibt Pfade und kurze Zusammenfassungen zurück.
    """
    import knowledge as k
    results = k.search(query)

    if SCOPE == "work":
        results = [r for r in results if r.get("topic") in _WORK_TOPICS]

    if not results:
        return "Kein passendes Wissen gefunden."

    lines = []
    for r in results:
        lines.append(f"- {r['path']}: {r.get('summary', '(keine Zusammenfassung)')}")
    return "\n".join(lines)


@mcp.tool()
def jarvis_read_knowledge(topic: str, file: str) -> str:
    """
    Liest eine Datei aus der JARVIS-Wissensdatenbank.
    topic: z.B. 'programmierung', 'digital35', 'simon', 'sport'
    file: z.B. 'security', '_core', 'standards', '_summary'
    """
    if not _check_scope(topic):
        return f"Zugriff verweigert: topic '{topic}' ist im scope '{SCOPE}' nicht erlaubt."

    import knowledge as k
    content = k.read(topic, file)
    if content is None:
        return f"Nicht gefunden: {topic}/{file}.md"
    if not content.strip():
        return f"Datei existiert, ist aber leer: {topic}/{file}.md"
    links = k.get_links(topic, file)
    if links["outgoing"] or links["backlinks"]:
        content += "\n\n---\n"
        if links["outgoing"]:
            content += f"Verlinkt mit: {', '.join(links['outgoing'])}\n"
        if links["backlinks"]:
            content += f"Verlinkt von: {', '.join(links['backlinks'])}\n"
    return content


@mcp.tool()
def jarvis_write_knowledge(topic: str, file: str, content: str, heading: str = "") -> str:
    """
    Speichert Wissen dauerhaft in JARVIS's Wissensdatenbank.
    Aufrufen wenn etwas Wichtiges gelernt, entschieden oder gelöst wurde.

    topic: Themenbereich (z.B. 'programmierung', 'digital35')
    file: Dateiname ohne .md (z.B. 'security', 'worklog', 'standards')
    content: Markdown-Inhalt — verwandte Dateien mit [[topic/file]] bzw. [[topic/file|Anzeigetext]]
             verlinken wenn eine echte inhaltliche Beziehung besteht. Backlinks werden automatisch
             berechnet, nicht selbst pflegen.
    heading: wenn angegeben → als neuer Abschnitt anhängen statt überschreiben
    """
    if not _check_scope(topic):
        return f"Schreibzugriff verweigert: topic '{topic}' ist im scope '{SCOPE}' nicht erlaubt."

    import knowledge as k
    if heading:
        k.append_section(topic, file, heading, content)
        return f"Abschnitt '{heading}' zu knowledge/{topic}/{file}.md hinzugefügt."
    else:
        k.write(topic, file, content)
        return f"Gespeichert: knowledge/{topic}/{file}.md"


@mcp.tool()
def jarvis_log_work(summary: str, topic: str = "digital35") -> str:
    """
    Schnelles Work-Update — was wurde heute erledigt/gelernt.
    Am Ende einer Arbeits-Session aufrufen.
    Wird in knowledge/{topic}/worklog.md gespeichert.
    """
    from datetime import date
    import knowledge as k

    today = date.today().isoformat()
    k.append_section(topic, "worklog", f"Work-Log {today}", summary)
    return f"Work-Log gespeichert ({today}): {summary[:80]}{'…' if len(summary) > 80 else ''}"


@mcp.tool()
def jarvis_read_project_file(path: str) -> str:
    """
    Liest eine Datei aus dem JARVIS-Server-Repo direkt.
    Nützlich um Architektur, Protokoll oder spezifische Implementierungen nachzuschlagen.
    path: relativer Pfad innerhalb des j.a.r.v.i.s. Repos (z.B. 'protocol.py', 'ARCHITECTURE.md')
    """
    if SCOPE == "work":
        return "jarvis_read_project_file ist nur im personal-scope verfügbar."

    base = Path(__file__).parent
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        return "Zugriff außerhalb des Repos nicht erlaubt."
    if not target.exists():
        return f"Datei nicht gefunden: {path}"
    try:
        content = target.read_text(encoding="utf-8")
        if len(content) > 8000:
            content = content[:8000] + f"\n\n[… gekürzt, Datei hat {len(content)} Zeichen]"
        return content
    except Exception as e:
        return f"Lesefehler: {e}"


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                        help="Transport: stdio (lokal) oder sse (Tailscale-Remote)")
    parser.add_argument("--port", type=int, default=8766, help="Port für SSE-Transport")
    parser.add_argument("--host", default="0.0.0.0", help="Host für SSE-Transport")
    args = parser.parse_args()

    print(f"[mcp] JARVIS MCP Server startet — scope={SCOPE}, transport={args.transport}", file=sys.stderr, flush=True)

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
