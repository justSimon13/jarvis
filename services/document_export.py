"""
Erzeugt PDF- oder Word-Dokumente aus einem Projekt oder einer Seite (lokales
local_data-Schema, siehe local_data.py: projekte/seiten-Tabellen).

Ein gemeinsamer, bewusst einfacher Markdown-Block-Parser (_parse_blocks) speist
zwei Renderer — reportlab für PDF, python-docx für Word. Kein HTML-Zwischenschritt
und keine Systemabhängigkeiten (anders als z.B. weasyprint/pandoc): beide Libraries
sind pip-only, passt zu "kein Docker"/einfacher Deploy. Deckt Überschriften (#/##/###),
Absätze und Bullet-Listen ab — reicht für JARVIS' Seiten-Inhalte (Notizen/PRDs/
Recherche), keine Tabellen/Bilder/Code-Blöcke in dieser ersten Version.
"""
import base64
import io
import re

import local_data

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def _parse_blocks(markdown_text: str) -> list[dict]:
    """Zerlegt Markdown in Blöcke: Überschrift ({"type":"h","level":N,"text":...}),
    Bullet-Liste ({"type":"ul","items":[...]}), sonst Absatz ({"type":"p","text":...})
    aus leerzeilengetrennten Zeilen."""
    blocks = []
    lines = (markdown_text or "").splitlines()
    paragraph_buf = []

    def flush_paragraph():
        text = " ".join(paragraph_buf).strip()
        if text:
            blocks.append({"type": "p", "text": text})
        paragraph_buf.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        heading_match = _HEADING_RE.match(stripped)
        bullet_match = _BULLET_RE.match(stripped)
        if not stripped:
            flush_paragraph()
        elif heading_match:
            flush_paragraph()
            blocks.append({"type": "h", "level": len(heading_match.group(1)), "text": heading_match.group(2).strip()})
        elif bullet_match:
            flush_paragraph()
            items = [bullet_match.group(1).strip()]
            while i + 1 < len(lines) and _BULLET_RE.match(lines[i + 1].strip()):
                i += 1
                items.append(_BULLET_RE.match(lines[i].strip()).group(1).strip())
            blocks.append({"type": "ul", "items": items})
        else:
            paragraph_buf.append(stripped)
        i += 1
    flush_paragraph()
    return blocks


def _split_bold(text: str) -> list[tuple[str, bool]]:
    """Zerlegt Text an **bold**-Markierungen in (Text, ist_fett)-Segmente."""
    segments = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], False))
        segments.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], False))
    return segments or [(text, False)]


def _blocks_for_section(title: str, markdown_text: str) -> list[dict]:
    """_parse_blocks(), aber verwirft eine führende Überschrift, die nur den
    Section-Titel wiederholt — Seiten fangen ihren Inhalt oft mit '# <eigener
    Titel>' an, sonst stünde der Titel doppelt im Dokument (einmal als
    Section-Heading, einmal als erster Content-Block)."""
    blocks = _parse_blocks(markdown_text)
    if blocks and blocks[0]["type"] == "h" and blocks[0]["text"].strip().lower() == (title or "").strip().lower():
        blocks = blocks[1:]
    return blocks


def _collect_seite_tree(seite_id: int, depth: int = 1) -> list[tuple[str, str, int]]:
    """(titel, inhalt, tiefe) für eine Seite + alle Unterseiten, rekursiv,
    Tiefe-zuerst-Reihenfolge (passt zur Anzeigereihenfolge im Frontend)."""
    seite = local_data.get_seite(seite_id)
    if not seite:
        return []
    result = [(seite["titel"], seite.get("inhalt") or "", depth)]
    for kind in local_data.list_unterseiten(seite_id):
        result.extend(_collect_seite_tree(kind["id"], depth + 1))
    return result


def _sections_for(quelle_typ: str, quelle_id: int) -> tuple[str, str, list[tuple[str, str, int]]]:
    """Gibt (Dokumenttitel, Intro-Markdown, [(Abschnittstitel, Inhalt, Tiefe), ...]) zurück."""
    if quelle_typ == "projekt":
        projekt = next((p for p in local_data.list_projekte() if p["id"] == quelle_id), None)
        if not projekt:
            raise ValueError(f"Projekt {quelle_id} nicht gefunden.")
        intro = projekt.get("beschreibung") or ""
        if projekt.get("notizen"):
            intro = (intro + "\n\n" + projekt["notizen"]).strip()
        children = []
        for kind in local_data.list_seiten("projekte", quelle_id):
            children.extend(_collect_seite_tree(kind["id"]))
        return projekt["name"], intro, children

    if quelle_typ == "seite":
        tree = _collect_seite_tree(quelle_id, depth=0)
        if not tree:
            raise ValueError(f"Seite {quelle_id} nicht gefunden.")
        titel, inhalt, _ = tree[0]
        children = [(t, c, max(d, 1)) for (t, c, d) in tree[1:]]
        return titel, inhalt, children

    raise ValueError(f"Unbekannter quelle_typ: {quelle_typ} (erlaubt: projekt, seite)")


def _render_pdf(doc_title: str, intro: str, children: list[tuple[str, str, int]]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=doc_title)
    styles = getSampleStyleSheet()
    heading_styles = {1: styles["Heading1"], 2: styles["Heading2"], 3: styles["Heading3"]}

    def blocks_to_flowables(blocks):
        flowables = []
        for block in blocks:
            if block["type"] == "h":
                flowables.append(Paragraph(_escape_reportlab(block["text"]), heading_styles.get(block["level"], styles["Heading3"])))
            elif block["type"] == "p":
                flowables.append(Paragraph(_markup_reportlab(block["text"]), styles["BodyText"]))
            elif block["type"] == "ul":
                flowables.append(ListFlowable(
                    [ListItem(Paragraph(_markup_reportlab(item), styles["BodyText"])) for item in block["items"]],
                    bulletType="bullet",
                ))
            flowables.append(Spacer(1, 6))
        return flowables

    story = [Paragraph(_escape_reportlab(doc_title), styles["Title"]), Spacer(1, 16)]
    story.extend(blocks_to_flowables(_blocks_for_section(doc_title, intro)))
    for title, content, depth in children:
        story.append(Spacer(1, 8))
        story.append(Paragraph(_escape_reportlab(title), heading_styles.get(min(depth, 3), styles["Heading3"])))
        story.extend(blocks_to_flowables(_blocks_for_section(title, content)))

    doc.build(story)
    return buf.getvalue()


def _escape_reportlab(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markup_reportlab(text: str) -> str:
    parts = []
    for chunk, bold in _split_bold(text):
        chunk = _escape_reportlab(chunk)
        parts.append(f"<b>{chunk}</b>" if bold else chunk)
    return "".join(parts)


def _render_docx(doc_title: str, intro: str, children: list[tuple[str, str, int]]) -> bytes:
    document = Document()
    document.add_heading(doc_title, level=0)

    def add_blocks(blocks):
        for block in blocks:
            if block["type"] == "h":
                document.add_heading(block["text"], level=min(block["level"] + 1, 4))
            elif block["type"] == "p":
                paragraph = document.add_paragraph()
                for chunk, bold in _split_bold(block["text"]):
                    run = paragraph.add_run(chunk)
                    run.bold = bold
            elif block["type"] == "ul":
                for item in block["items"]:
                    paragraph = document.add_paragraph(style="List Bullet")
                    for chunk, bold in _split_bold(item):
                        run = paragraph.add_run(chunk)
                        run.bold = bold

    add_blocks(_blocks_for_section(doc_title, intro))
    for title, content, depth in children:
        document.add_heading(title, level=min(depth + 1, 4))
        add_blocks(_blocks_for_section(title, content))

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def generate(quelle_typ: str, quelle_id: int, format: str) -> tuple[str, str, str]:
    """Gibt (filename, mime_type, data_base64) zurück. Wirft ValueError bei
    unbekannter Quelle/unbekanntem Format."""
    if format not in ("pdf", "docx"):
        raise ValueError(f"Unbekanntes Format: {format} (erlaubt: pdf, docx)")

    doc_title, intro, children = _sections_for(quelle_typ, quelle_id)

    if format == "pdf":
        data = _render_pdf(doc_title, intro, children)
        mime = "application/pdf"
    else:
        data = _render_docx(doc_title, intro, children)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    safe_name = _SAFE_FILENAME_RE.sub("_", doc_title).strip("_") or "dokument"
    filename = f"{safe_name}.{format}"
    return filename, mime, base64.b64encode(data).decode("ascii")
