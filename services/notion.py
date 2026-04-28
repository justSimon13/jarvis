from datetime import date as date_cls
from pathlib import Path
from notion_client import Client as NotionClient
import config
import context

# Registry: logischer Name → DB-ID + Schema
REGISTRY = {
    "todos": {
        "db_id": config.NOTION_TODOS_DB_ID,
        "cache_key": "todos",
        "title_field": "Name",
        "properties": {
            "Name":      "title",
            "Status":    "status",
            "Datum":     "date",
            "Priorität": "select",
            "Bereich":   "select",
            "Aufwand":   "select",
        },
    },
    "projekte": {
        "db_id": config.NOTION_PROJEKTE_DB_ID,
        "cache_key": "projekte",
        "title_field": "Projekt",
        "properties": {
            "Projekt":      "title",
            "Status":       "select",
            "Beschreibung": "rich_text",
            "Typ":          "select",
        },
    },
    "konzepte": {
        "db_id": config.NOTION_KONZEPTE_DB_ID,
        "cache_key": "konzepte",
        "title_field": "Name",
        "properties": {
            "Name":   "title",
            "Status": "select",
            "Notiz":  "rich_text",
            "Typ":    "select",
        },
    },
    "kontakte": {
        "db_id": config.NOTION_KONTAKTE_DB_ID,
        "cache_key": "kontakte",
        "title_field": "Name",
        "properties": {
            "Name":              "title",
            "Email":             "text",
            "Tel. Nummer":       "text",
            "Mehrfachauswahl":   "multi_select",
        },
    },
}

_client = None


def _get_client() -> NotionClient:
    global _client
    if _client is None:
        _client = NotionClient(auth=config.NOTION_API_KEY)
    return _client


def _to_notion_props(database: str, properties: dict) -> dict:
    schema = REGISTRY[database]["properties"]
    result = {}
    for key, value in properties.items():
        prop_type = schema.get(key)
        if prop_type == "title":
            result[key] = {"title": [{"text": {"content": str(value)}}]}
        elif prop_type == "status":
            result[key] = {"status": {"name": str(value)}}
        elif prop_type == "select":
            result[key] = {"select": {"name": str(value)}}
        elif prop_type == "date":
            result[key] = {"date": {"start": str(value)}}
        elif prop_type == "rich_text":
            result[key] = {"rich_text": [{"text": {"content": str(value)}}]}
    return result


def _from_notion_page(page: dict, database: str) -> dict:
    schema = REGISTRY[database]["properties"]
    result = {"page_id": page["id"]}
    props = page.get("properties", {})
    for key, prop_type in schema.items():
        raw = props.get(key, {})
        try:
            if prop_type == "title":
                items = raw.get("title", [])
                result[key] = items[0]["plain_text"] if items else None
            elif prop_type == "status":
                s = raw.get("status")
                result[key] = s["name"] if s else None
            elif prop_type == "select":
                s = raw.get("select")
                result[key] = s["name"] if s else None
            elif prop_type == "date":
                d = raw.get("date")
                result[key] = d["start"] if d else None
            elif prop_type == "rich_text":
                items = raw.get("rich_text", [])
                result[key] = items[0]["plain_text"] if items else None
            elif prop_type == "text":
                items = raw.get("rich_text", [])
                result[key] = items[0]["plain_text"] if items else None
            elif prop_type == "multi_select":
                result[key] = [o["name"] for o in raw.get("multi_select", [])]
        except (KeyError, IndexError, TypeError):
            result[key] = None
    return result


def query(database: str, search: str = None, status: str = None, limit: int = 10) -> list[dict]:
    if database not in REGISTRY:
        raise ValueError(f"Unbekannte Datenbank: {database}. Verfügbar: {list(REGISTRY)}")
    db_id = REGISTRY[database]["db_id"]
    title_field = REGISTRY[database]["title_field"]
    filters = []
    if search:
        filters.append({"property": title_field, "title": {"contains": search}})
    if status:
        prop_type = REGISTRY[database]["properties"].get("Status")
        if prop_type == "status":
            filters.append({"property": "Status", "status": {"equals": status}})
        elif prop_type == "select":
            filters.append({"property": "Status", "select": {"equals": status}})

    kwargs = {"database_id": db_id, "page_size": limit}
    if len(filters) == 1:
        kwargs["filter"] = filters[0]
    elif len(filters) > 1:
        kwargs["filter"] = {"and": filters}

    response = _get_client().databases.query(**kwargs)
    return [_from_notion_page(p, database) for p in response.get("results", [])]


def write(database: str, properties: dict, content: list[dict] | None = None) -> str:
    if database not in REGISTRY:
        raise ValueError(f"Unbekannte Datenbank: {database}")
    notion_props = _to_notion_props(database, properties)
    kwargs = {
        "parent": {"database_id": REGISTRY[database]["db_id"]},
        "properties": notion_props,
    }
    if content:
        kwargs["children"] = _to_blocks(content)
    page = _get_client().pages.create(**kwargs)
    context.invalidate(REGISTRY[database]["cache_key"])
    return page["id"]


def update(page_id: str, database: str, properties: dict) -> None:
    if database not in REGISTRY:
        raise ValueError(f"Unbekannte Datenbank: {database}")
    notion_props = _to_notion_props(database, properties)
    _get_client().pages.update(page_id=page_id, properties=notion_props)
    context.invalidate(REGISTRY[database]["cache_key"])


def _to_blocks(items: list[dict]) -> list[dict]:
    """Konvertiert einfache Block-Beschreibungen in Notion API Format."""
    result = []
    for item in items:
        t = item.get("type", "paragraph")
        text = item.get("text", "")
        rich = [{"type": "text", "text": {"content": text}}]
        if t == "to_do":
            result.append({"object": "block", "type": "to_do",
                           "to_do": {"rich_text": rich, "checked": item.get("checked", False)}})
        elif t == "bullet":
            result.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": rich}})
        elif t == "heading":
            result.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": rich}})
        else:
            result.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": rich}})
    return result


def _fetch_all_blocks(page_id: str) -> list:
    client = _get_client()
    blocks = []
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        blocks.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return blocks


def _backup_dir():
    import config as _cfg
    d = _cfg.JARVIS_DIR / "notion_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_page(page_id: str) -> Path:
    """Sichert alle Blöcke einer Seite lokal. Gibt den Backup-Pfad zurück."""
    import json
    from datetime import datetime
    blocks = _fetch_all_blocks(page_id)
    safe_id = page_id.replace("-", "")[:16]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _backup_dir() / f"{safe_id}_{ts}.json"
    path.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def restore_page(page_id: str) -> str:
    """Stellt eine Seite aus dem neuesten lokalen Backup wieder her."""
    import json
    safe_id = page_id.replace("-", "")[:16]
    backups = sorted(_backup_dir().glob(f"{safe_id}_*.json"), reverse=True)
    if not backups:
        return "Kein Backup für diese Seite gefunden."
    blocks = json.loads(backups[0].read_text(encoding="utf-8"))

    restored = []
    for block in blocks:
        t = block.get("type")
        content = block.get(t, {})
        new_block = {"object": "block", "type": t}
        if "rich_text" in content:
            new_block[t] = {"rich_text": content["rich_text"]}
            if t == "to_do":
                new_block[t]["checked"] = content.get("checked", False)
            elif t == "code":
                new_block[t]["language"] = content.get("language", "plain text")
        elif t == "divider":
            new_block[t] = {}
        else:
            continue
        restored.append(new_block)

    if not restored:
        return "Backup enthält keine wiederherstellbaren Blöcke."

    clear_page(page_id)
    client = _get_client()
    for i in range(0, len(restored), 100):
        client.blocks.children.append(block_id=page_id, children=restored[i:i + 100])

    return f"{len(restored)} Blöcke aus {backups[0].name} wiederhergestellt."


def clear_page(page_id: str) -> int:
    """Löscht alle Blöcke einer Notion-Seite. Erstellt vorher automatisch ein Backup."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    backup_page(page_id)
    blocks = _fetch_all_blocks(page_id)
    client = _get_client()

    def _delete(block_id):
        try:
            client.blocks.update(block_id=block_id, archived=True)
            return True
        except Exception:
            return False

    deleted = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(_delete, b["id"]) for b in blocks]
        for f in as_completed(futures):
            if f.result():
                deleted += 1
    return deleted


def append_blocks(page_id: str, blocks: list[dict]) -> None:
    """Fügt Blöcke an eine bestehende Notion-Seite an. Batcht automatisch bei > 100 Blöcken."""
    notion_blocks = _to_blocks(blocks)
    client = _get_client()
    for i in range(0, len(notion_blocks), 100):
        client.blocks.children.append(block_id=page_id, children=notion_blocks[i:i + 100])


def _block_to_text(block: dict, with_id: bool = False) -> str:
    t = block.get("type", "")
    data = block.get(t, {})
    rt = data.get("rich_text", [])
    text = "".join(x.get("plain_text", "") for x in rt)
    if not text and t not in ("divider", "table_of_contents"):
        return ""
    if t == "heading_1":
        rendered = f"# {text}"
    elif t == "heading_2":
        rendered = f"## {text}"
    elif t == "heading_3":
        rendered = f"### {text}"
    elif t == "bulleted_list_item":
        rendered = f"- {text}"
    elif t == "numbered_list_item":
        rendered = f"• {text}"
    elif t == "to_do":
        done = "✓" if data.get("checked") else "○"
        rendered = f"{done} {text}"
    elif t == "quote":
        rendered = f"> {text}"
    elif t == "code":
        rendered = f"```{data.get('language', '')}\n{text}\n```"
    elif t == "callout":
        icon = (data.get("icon") or {}).get("emoji", "")
        rendered = f"{icon} {text}".strip()
    elif t == "divider":
        rendered = "---"
    else:
        rendered = text
    if with_id:
        return f"[id:{block['id']}] {rendered}"
    return rendered


def read_page(page_id: str, max_blocks: int = 200, with_ids: bool = False) -> str:
    """Liest den Textinhalt einer Notion-Seite. with_ids=True gibt Block-IDs mit aus."""
    client = _get_client()
    lines = []
    cursor = None
    fetched = 0
    while fetched < max_blocks:
        kwargs = {"block_id": page_id, "page_size": min(100, max_blocks - fetched)}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response.get("results", []):
            line = _block_to_text(block, with_id=with_ids)
            if line:
                lines.append(line)
        fetched += len(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return "\n".join(lines) if lines else "(Seite hat keinen lesbaren Textinhalt)"


def update_block(block_id: str, text: str, block_type: str = "paragraph") -> None:
    """Ändert den Textinhalt eines bestehenden Blocks."""
    type_map = {
        "paragraph": "paragraph",
        "heading": "heading_2",
        "heading_1": "heading_1",
        "heading_2": "heading_2",
        "heading_3": "heading_3",
        "bullet": "bulleted_list_item",
        "numbered": "numbered_list_item",
        "to_do": "to_do",
        "quote": "quote",
    }
    notion_type = type_map.get(block_type, block_type)
    rich = [{"type": "text", "text": {"content": text}}]
    payload = {notion_type: {"rich_text": rich}}
    if notion_type == "to_do":
        payload[notion_type]["checked"] = False
    _get_client().blocks.update(block_id=block_id, **payload)


def delete_blocks(block_ids: list[str]) -> int:
    """Löscht mehrere Blöcke parallel. Gibt die Anzahl gelöschter Blöcke zurück."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    client = _get_client()

    def _delete(bid):
        try:
            client.blocks.update(block_id=bid, archived=True)
            return True
        except Exception:
            return False

    deleted = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for f in as_completed([ex.submit(_delete, bid) for bid in block_ids]):
            if f.result():
                deleted += 1
    return deleted


def search_pages(query: str, limit: int = 5) -> list[dict]:
    """Sucht beliebige Notion-Seiten nach Titel (nicht nur Datenbank-Einträge)."""
    response = _get_client().search(
        query=query,
        filter={"value": "page", "property": "object"},
        page_size=limit,
    )
    results = []
    for page in response.get("results", []):
        props = page.get("properties", {})
        title = ""
        for prop in props.values():
            if prop.get("type") == "title":
                items = prop.get("title", [])
                title = items[0]["plain_text"] if items else ""
                break
        if not title:
            title_list = page.get("properties", {}).get("title", {}).get("title", [])
            title = title_list[0]["plain_text"] if title_list else "(kein Titel)"
        results.append({"page_id": page["id"], "title": title, "url": page.get("url", "")})
    return results


def sync_vip_emails() -> list[str]:
    """Zieht alle Kontakte mit Mehrfachauswahl=Kunde und gibt ihre Email-Adressen zurück."""
    response = _get_client().databases.query(
        database_id=config.NOTION_KONTAKTE_DB_ID,
        filter={"property": "Mehrfachauswahl", "multi_select": {"contains": "Kunde"}},
    )
    _GENERIC_DOMAINS = {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "yahoo.com", "yahoo.de", "gmx.de", "gmx.net", "gmx.at", "web.de",
        "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me",
    }
    vip_patterns = []
    for page in response.get("results", []):
        props = page.get("properties", {})
        email_items = props.get("Email", {}).get("rich_text", [])
        if email_items:
            addr = email_items[0]["plain_text"].strip().lower()
            if addr:
                domain = addr.split("@")[-1] if "@" in addr else addr
                vip_patterns.append(addr if domain in _GENERIC_DOMAINS else domain)
    return vip_patterns


def delete(page_id: str, database: str = None) -> None:
    _get_client().pages.update(page_id=page_id, archived=True)
    if database and database in REGISTRY:
        context.invalidate(REGISTRY[database]["cache_key"])
