from datetime import date as date_cls
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


def write(database: str, properties: dict) -> str:
    if database not in REGISTRY:
        raise ValueError(f"Unbekannte Datenbank: {database}")
    notion_props = _to_notion_props(database, properties)
    page = _get_client().pages.create(
        parent={"database_id": REGISTRY[database]["db_id"]},
        properties=notion_props,
    )
    context.invalidate(REGISTRY[database]["cache_key"])
    return page["id"]


def update(page_id: str, database: str, properties: dict) -> None:
    if database not in REGISTRY:
        raise ValueError(f"Unbekannte Datenbank: {database}")
    notion_props = _to_notion_props(database, properties)
    _get_client().pages.update(page_id=page_id, properties=notion_props)
    context.invalidate(REGISTRY[database]["cache_key"])


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
