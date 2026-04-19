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
def stream(system_prompt: str, messages: list[dict], tools: list[dict] = None):
    kwargs = {"tools": tools} if tools else {}
    with _get_client().messages.stream(
        model=MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
        **kwargs,
    ) as s:
        yield s
