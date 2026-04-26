from contextlib import contextmanager
import anthropic
import config

_client = None
MODEL = "claude-sonnet-4-6"


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


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


@contextmanager
def stream(system_static: str, system_dynamic: str, messages: list[dict], tools: list[dict] = None):
    system = [
        {"type": "text", "text": system_static, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": system_dynamic},
    ]

    cached_tools = None
    if tools:
        cached_tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

    with _get_client().messages.stream(
        model=MODEL,
        max_tokens=8096,
        system=system,
        messages=messages,
        **({"tools": cached_tools} if cached_tools else {}),
    ) as s:
        yield s
