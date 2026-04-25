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
        max_tokens=1024,
        system=system,
        messages=messages,
        **({"tools": cached_tools} if cached_tools else {}),
    ) as s:
        yield s
