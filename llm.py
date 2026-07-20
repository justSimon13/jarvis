from contextlib import contextmanager
import anthropic
import config

_client = None
MODEL = "claude-sonnet-4-6"

# Preise in USD je 1M Tokens (Sonnet-Tarif, deckt sowohl 4.6 als auch 5 ab —
# identische Preise). 1h-Cache-TTL statt der 5-Minuten-Default: Nachrichten im
# normalen Gespräch liegen typischerweise mehrere Minuten auseinander (echtes
# Reden, keine Automatisierung) — bei 5 Minuten kühlt der Cache ständig ab,
# jede erneute Nachricht zahlt dann den vollen Neuschreib-Preis für System-
# Prompt + alle Tool-Schemas. 1h-Schreiben kostet zwar mehr (2× statt 1,25×
# Grundpreis), aber deutlich seltener — netto günstiger bei diesem Nutzungsmuster.
# Kein Beta-Header nötig, ttl:"1h" ist inzwischen regulärer API-Standard.
_PRICE_PER_M_INPUT = 3.00
_PRICE_PER_M_OUTPUT = 15.00
_PRICE_PER_M_CACHE_WRITE = _PRICE_PER_M_INPUT * 2.0
_PRICE_PER_M_CACHE_READ = _PRICE_PER_M_INPUT * 0.10


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def compute_cost(usage) -> float:
    """Kosten eines einzelnen API-Calls in USD, aus dem usage-Objekt der Anthropic-Antwort.
    Nur eine Schätzung auf Basis der Listenpreise — für die exakte Abrechnung zählt die
    Anthropic Console (gleiche Einschränkung wie bei der Coding-Engine-Kostenschätzung)."""
    if not usage:
        return 0.0
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        input_tokens * _PRICE_PER_M_INPUT
        + output_tokens * _PRICE_PER_M_OUTPUT
        + cache_write * _PRICE_PER_M_CACHE_WRITE
        + cache_read * _PRICE_PER_M_CACHE_READ
    ) / 1_000_000
    return cost


def compress_tool_history(messages: list[dict]) -> list[dict]:
    """Komprimiert Tool-Results in alten Nachrichten (alle außer der letzten)."""
    last_tool_idx = -1
    for i, msg in enumerate(messages):
        if (msg.get("role") == "user"
                and isinstance(msg.get("content"), list)
                and any(c.get("type") == "tool_result" for c in msg["content"])):
            last_tool_idx = i

    result = []
    for i, msg in enumerate(messages):
        if (i < last_tool_idx
                and msg.get("role") == "user"
                and isinstance(msg.get("content"), list)
                and any(c.get("type") == "tool_result" for c in msg["content"])):
            new_content = [_compress_one(item) for item in msg["content"]]
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def _compress_one(item: dict) -> dict:
    if item.get("type") != "tool_result":
        return item
    content = item.get("content", "")
    if not isinstance(content, str) or len(content) <= 400:
        return item
    if "[id:" in content[:80] or content.startswith("# ") or content.startswith("## "):
        lines = content.count("\n") + 1
        return {**item, "content": f"[Seiteninhalt: {lines} Blöcke, {len(content)} Zeichen — bereits verarbeitet]"}
    if content.lstrip().startswith("[") or content.lstrip().startswith("{"):
        return {**item, "content": content[:300] + f"… [{len(content)} Zeichen, gekürzt]"}
    return {**item, "content": content[:300] + f"… [gekürzt]"}


def compress_attachment_history(messages: list[dict]) -> list[dict]:
    """Ersetzt Bild-/Dokument-Anhänge in allen außer der letzten Anhang-Nachricht
    durch Platzhalter — sonst wird bei jedem weiteren Turn wieder das komplette
    Base64 mitgeschickt (Tokens/Bandbreite). Gleiches Muster wie compress_tool_history."""
    last_attachment_idx = -1
    for i, msg in enumerate(messages):
        if (msg.get("role") == "user"
                and isinstance(msg.get("content"), list)
                and any(c.get("type") in ("image", "document") for c in msg["content"])):
            last_attachment_idx = i

    result = []
    for i, msg in enumerate(messages):
        if (i < last_attachment_idx
                and msg.get("role") == "user"
                and isinstance(msg.get("content"), list)):
            new_content = [_compress_attachment(c) for c in msg["content"]]
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def _compress_attachment(item: dict) -> dict:
    if item.get("type") == "image":
        return {"type": "text", "text": "[Bild angehängt — bereits verarbeitet]"}
    if item.get("type") == "document":
        return {"type": "text", "text": "[Dokument angehängt — bereits verarbeitet]"}
    return item


@contextmanager
def stream(system_static: str, system_dynamic: str, messages: list[dict], tools: list[dict] = None):
    system = [
        {"type": "text", "text": system_static, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": system_dynamic},
    ]

    cached_tools = None
    if tools:
        cached_tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral", "ttl": "1h"}}]

    with _get_client().messages.stream(
        model=MODEL,
        max_tokens=8096,
        system=system,
        messages=messages,
        **({"tools": cached_tools} if cached_tools else {}),
    ) as s:
        yield s
