from contextlib import contextmanager
import anthropic
import config

_client = None
MODEL = "claude-sonnet-5"

# Modelle, die JARVIS' Chat-Pipeline zur Wahl stellt (Preis in USD je 1M Tokens,
# Input/Output — Stand 2026-07-25, Sonnet-5-Preis inkl. Einführungsrabatt bis
# 2026-08-31 ($2/$10, danach $3/$15 wie hier hinterlegt). Jedes Modell hat einen
# eigenen Preis — compute_cost() schlägt darüber nach, nicht mehr fest verdrahtet
# auf den Sonnet-Tarif, sonst wäre die Kostenanzeige bei Opus/Haiku/Fable falsch.
MODEL_CATALOG = {
    "claude-haiku-4-5": {"label": "Haiku 4.5", "input": 1.00, "output": 5.00},
    "claude-sonnet-5":  {"label": "Sonnet 5",  "input": 3.00, "output": 15.00},
    "claude-opus-5":    {"label": "Opus 5",    "input": 5.00, "output": 25.00},
    "claude-fable-5":   {"label": "Fable 5",   "input": 10.00, "output": 50.00},
}

# 1h-Cache-TTL statt der 5-Minuten-Default: Nachrichten im normalen Gespräch liegen
# typischerweise mehrere Minuten auseinander (echtes Reden, keine Automatisierung) —
# bei 5 Minuten kühlt der Cache ständig ab, jede erneute Nachricht zahlt dann den
# vollen Neuschreib-Preis für System-Prompt + alle Tool-Schemas. 1h-Schreiben kostet
# zwar mehr (2× statt 1,25× Grundpreis), aber deutlich seltener — netto günstiger bei
# diesem Nutzungsmuster. Kein Beta-Header nötig, ttl:"1h" ist regulärer API-Standard.
# Cache-Multiplikatoren sind proportional zum jeweiligen Input-Preis des Modells,
# siehe compute_cost().
_CACHE_WRITE_MULT = 2.0
_CACHE_READ_MULT = 0.10


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Explizites Timeout — ohne das kann ein einzelner hängender Call (Netzwerk-
        # Stall, API-Problem) den globalen llm_semaphore (server.py, Semaphore(1) für
        # ALLE Clients) auf unbestimmte Zeit blockieren: die pipeline.py-Fehlerbehandlung
        # rund um llm.stream() fängt zwar jede Exception ab und gibt den Semaphore
        # wieder frei, aber nur wenn überhaupt eine Exception kommt — ohne Timeout
        # wartet der SDK-Default (deutlich länger, im Zweifel unbrauchbar lang für
        # einen Chat) einfach weiter, und in der Zwischenzeit hängt der komplette
        # Server für jeden Client fest (2026-07-22 live beobachtet: Chat reagierte
        # nach einem laufenden Coding-Task minutenlang gar nicht mehr).
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=120.0)
    return _client


def compute_cost(usage, model: str | None = None) -> float:
    """Kosten eines einzelnen API-Calls in USD, aus dem usage-Objekt der Anthropic-Antwort.
    Nur eine Schätzung auf Basis der Listenpreise — für die exakte Abrechnung zählt die
    Anthropic Console (gleiche Einschränkung wie bei der Coding-Engine-Kostenschätzung).
    model: welches Modell den Call tatsächlich bearbeitet hat — jedes hat einen eigenen
    Preis (siehe MODEL_CATALOG), Fallback auf MODEL falls unbekannt/nicht angegeben."""
    if not usage:
        return 0.0
    pricing = MODEL_CATALOG.get(model, MODEL_CATALOG[MODEL])
    price_in = pricing["input"]
    price_out = pricing["output"]
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        input_tokens * price_in
        + output_tokens * price_out
        + cache_write * price_in * _CACHE_WRITE_MULT
        + cache_read * price_in * _CACHE_READ_MULT
    ) / 1_000_000
    return cost


def complete(system: str, user_text: str, model: str = "claude-haiku-4-5", max_tokens: int = 60) -> tuple[str, object]:
    """Minimaler, NICHT-streamender Einzel-Call für billige Hintergrund-Aufgaben
    (z.B. automatische Thread-Benennung, siehe thread_naming.py) — kein
    Tool-Loop, kein Cache-Control, kein fester 24000-Token-Deckel wie stream().
    Gibt (Text, usage-Objekt) zurück — usage kann optional durch compute_cost()
    laufen, nur fürs Logging."""
    response = _get_client().messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    return text.strip(), response.usage


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


def _item_type(item):
    """type-Feld eines Content-Blocks — funktioniert sowohl für rohe Anthropic-SDK-
    Objekte (frisch aus final.content einer Antwort, Attribut-Zugriff) als auch für
    reine Dicts (aus SQLite über session_memory geladen, dict-Zugriff). tool_use-Blöcke
    kommen in client_messages in beiden Formen vor, je nachdem ob sie aus der laufenden
    Session stammen oder aus der History nachgeladen wurden."""
    return item.get("type") if isinstance(item, dict) else getattr(item, "type", None)


def compress_tool_calls(messages: list[dict]) -> list[dict]:
    """Komprimiert große Text-Argumente in tool_use-Blöcken alter Assistant-Nachrichten —
    Pendant zu compress_tool_history, das nur die tool_result-Seite (User-Nachrichten)
    behandelt. Tools wie write_knowledge/write_seite/create_seite/append_knowledge_section
    bekommen ganze Dokumente als Argument mit; die blieben bisher für immer unkomprimiert
    im Verlauf, selbst wenn das zugehörige Ergebnis längst zum Platzhalter geschrumpft war
    (compress_tool_history griff dort einfach nie, weil sie in Assistant- statt
    User-Nachrichten stecken). Wie dort: alles außer dem jeweils letzten Vorkommen."""
    def has_tool_use(msg):
        return (msg.get("role") == "assistant"
                and isinstance(msg.get("content"), list)
                and any(_item_type(c) == "tool_use" for c in msg["content"]))

    last_idx = -1
    for i, msg in enumerate(messages):
        if has_tool_use(msg):
            last_idx = i

    result = []
    for i, msg in enumerate(messages):
        if i < last_idx and has_tool_use(msg):
            new_content = [_compress_tool_use(c) for c in msg["content"]]
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def _compress_tool_use(item):
    if _item_type(item) != "tool_use":
        return item
    tool_input = item.get("input") if isinstance(item, dict) else getattr(item, "input", None)
    if not isinstance(tool_input, dict):
        return item
    new_input = {}
    changed = False
    for key, value in tool_input.items():
        if isinstance(value, str) and len(value) > 400:
            new_input[key] = f"[{key}: {len(value)} Zeichen, bereits ausgeführt]"
            changed = True
        else:
            new_input[key] = value
    if not changed:
        return item
    base = item if isinstance(item, dict) else item.model_dump()
    return {**base, "input": new_input}


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
def stream(system_static: str, system_dynamic: str, messages: list[dict], tools: list[dict] = None,
           thinking: bool = False, model: str | None = None):
    """
    model: eines der Modelle aus MODEL_CATALOG — Fallback auf MODEL bei ungültigem/fehlendem Wert.
    thinking: Adaptive Thinking an/aus. Default aus, damit sich am bisherigen schnellen
    Antwortverhalten (Voice) nichts stillschweigend ändert. max_tokens wird bei aktivem
    Thinking angehoben, da Thinking-Tokens dort mit hineinzählen — bei zu knappem Wert könnte
    eine Antwort sonst mitten im Denkprozess abgeschnitten werden.

    max_tokens war bis 2026-07-31 bei 8096 (ohne Thinking) — zu knapp für eine Antwort, die mit
    einem Tool-Aufruf endet, dessen Inhalt gegen dasselbe Budget zählt wie sichtbarer Text (z.B.
    write_knowledge mit einem längeren Dokument). Reale Abbrüche mit stop_reason='max_tokens'
    GENAU an dieser Stelle beobachtet (der Aufruf wurde dabei nie ausgeführt, siehe pipeline.py::
    _find_truncated_tool_call). Angehoben auf Werte, die unter dem kleinsten unterstützten Modells
    (Haiku 4.5, max. 32000 Output-Tokens) bleiben — Sonnet/Opus 5 erlauben deutlich mehr (bis
    64000), Fable 5 ist unbekannt, bekommt hier denselben vorsichtigeren Wert wie Thinking. Ein
    höheres max_tokens kostet nichts zusätzlich, solange es nicht tatsächlich ausgeschöpft wird
    (Abrechnung nach TATSÄCHLICH generierten Tokens, nicht nach dem Limit selbst).

    Sonderfall Fable 5: erlaubt kein thinking={"type":"disabled"} (400 bei jedem Versuch,
    Thinking läuft dort immer). Der thinking-Parameter wird für dieses Modell ignoriert,
    nicht als Fehler behandelt — die UI deaktiviert den Thinking-Toggle bei Fable-5-Auswahl
    zusätzlich, das hier ist die serverseitige Absicherung falls trotzdem thinking=False
    ankommt (z.B. altes Frontend, direkter API-Aufruf).
    """
    model = model if model in MODEL_CATALOG else MODEL

    system = [
        {"type": "text", "text": system_static, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": system_dynamic},
    ]

    cached_tools = None
    if tools:
        cached_tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral", "ttl": "1h"}}]

    if model == "claude-fable-5":
        thinking_config = {"type": "adaptive"}
        max_tokens = 30000
    else:
        thinking_config = {"type": "adaptive"} if thinking else {"type": "disabled"}
        max_tokens = 30000 if thinking else 24000

    with _get_client().messages.stream(
        model=model,
        max_tokens=max_tokens,
        thinking=thinking_config,
        system=system,
        messages=messages,
        **({"tools": cached_tools} if cached_tools else {}),
    ) as s:
        yield s
