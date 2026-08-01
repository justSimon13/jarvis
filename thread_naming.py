"""
Automatische Benennung unbenannter Threads (Thread-Umbau Teil B, Schritt 1).

Zwei Einstiegspunkte, eine gemeinsame Kernfunktion (_attempt_naming):
- maybe_name_thread_on_turn(): Live-Hook aus pipeline.py, nach jedem abgeschlossenen
  Turn. Versucht nur bei Rundenzahl exakt 2, 4 oder 8 (Simons Vorgabe — deckt spät
  erkennbare Themen ab, bei weniger als der Hälfte der Aufrufe einer "jede Runde"-
  Variante). Läuft immer im eigenen Hintergrund-Thread (siehe pipeline.py), blockiert
  die bereits rausgegangene Chat-Antwort nicht.
- maybe_name_thread_once(): Startup-Sweep aus server.py::main(), einmalig pro
  bestehendem unbenannten Thread mit >= 2 Runden — Nachholtermin für Threads, in
  denen längst nicht mehr geschrieben wird und die der Live-Hook deshalb nie erreicht.

Nie überschreiben: session_memory.set_auto_title() schreibt nur bei title IS NULL,
atomar (siehe dort) — ein manuell vergebener Titel wird nie angetastet, unabhängig
davon ob ein Hintergrund-Call gerade parallel läuft.
"""
import time

import llm
import protocol as P
import session_memory

_MAX_PROMPT_CHARS = 4000
_MAX_TITLE_LEN = 60
_NAMING_MODEL = "claude-haiku-4-5"
_NAMING_MAX_TOKENS = 60
_ON_TURN_ROUNDS = (2, 4, 8)
_SWEEP_MIN_ROUNDS = 2
_SWEEP_THROTTLE_SECONDS = 3

_SYSTEM_PROMPT = """Du bekommst den bisherigen Verlauf eines Chat-Threads sowie eine Liste bereits vergebener Thread-Titel. Deine einzige Aufgabe: einen kurzen, treffenden Titel für DIESEN Thread vorschlagen — oder, falls das Thema noch nicht erkennbar ist, das Wort UNCLEAR.

Regeln:
- 2 bis 4 Wörter, deutsch, wie ein Aktenordner-Etikett ("Steuern", "Waschmaschine kaputt", "Angebot Kunde Meyer") — NICHT der Wortlaut der ersten Frage, NICHT ein ganzer Satz.
- Ist das Thema inhaltlich dasselbe wie einer der vorhandenen Titel, verwende GENAU diesen Titel, unverändert — keine Variante, keine Ergänzung, kein Zusatz.
- Ist nach dem bisherigen Verlauf noch kein klares Thema erkennbar (z.B. nur eine Begrüßung, eine sehr allgemeine Frage), antworte NUR mit UNCLEAR — kein anderer Text.
- Antworte NUR mit dem Titel (oder UNCLEAR). Keine Anführungszeichen, keine Erklärung, kein Punkt am Ende."""


def _build_user_prompt(thread_id: int) -> str | None:
    """None falls der Thread (mehr) keinen sichtbaren Verlauf hat — sollte bei
    Rundenzahl >= 2 nicht vorkommen, aber ein defensiver No-Op ist besser als
    ein kaputter Prompt."""
    messages = session_memory.get_thread_messages(thread_id)
    if not messages:
        return None
    # Der eigene Titel ist an dieser Stelle garantiert None (Aufrufer prüft das
    # vorher) und taucht damit nie in list_named_titles() auf — keine Filterung
    # des eigenen Threads nötig.
    existing_titles = session_memory.list_named_titles(limit=300)

    lines = []
    total_len = 0
    for m in messages:
        role_label = "User" if m["role"] == "user" else "Assistant"
        line = f"{role_label}: {m['text']}"
        if total_len + len(line) > _MAX_PROMPT_CHARS:
            break
        lines.append(line)
        total_len += len(line) + 1

    titles_block = "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "(noch keine)"
    return f"Bereits vergebene Titel:\n{titles_block}\n\nBisheriger Verlauf dieses Threads:\n" + "\n".join(lines)


def _attempt_naming(thread_id: int, broadcast) -> None:
    """Kernfunktion — keine eigene Rundenzahl-Prüfung, die liegt bei den beiden
    Aufrufern unten. broadcast: Callable[[dict], None], schickt eine Nachricht
    an ALLE verbundenen Web-/Dashboard-Clients (siehe server.py::_broadcast_web_event)."""
    try:
        if session_memory.get_thread_title(thread_id) is not None:
            return  # Sicherheitsnetz — Titel könnte zwischen Aufrufer-Check und hier gesetzt worden sein

        user_prompt = _build_user_prompt(thread_id)
        if not user_prompt:
            return

        raw_title, usage = llm.complete(_SYSTEM_PROMPT, user_prompt,
                                         model=_NAMING_MODEL, max_tokens=_NAMING_MAX_TOKENS)
        cost = llm.compute_cost(usage, model=_NAMING_MODEL)

        title = raw_title.strip().strip('"\'').strip()
        if not title or title.upper() == "UNCLEAR":
            print(f"[thread_naming] thread={thread_id}: UNCLEAR (${cost:.5f})", flush=True)
            return
        title = title[:_MAX_TITLE_LEN].rstrip()

        if session_memory.set_auto_title(thread_id, title):
            print(f"[thread_naming] thread={thread_id}: '{title}' (${cost:.5f})", flush=True)
            broadcast({"type": P.THREAD_TITLE_UPDATED, "thread_id": thread_id, "title": title})
        else:
            # Race verloren — Titel wurde zwischenzeitlich manuell gesetzt oder
            # der Thread gelöscht/gemergt. Kein Fehler, einfach nichts zu tun.
            print(f"[thread_naming] thread={thread_id}: Titel '{title}' verworfen (bereits belegt)", flush=True)
    except Exception as e:
        print(f"[thread_naming] Fehler bei thread={thread_id}: {e}", flush=True)


def maybe_name_thread_on_turn(thread_id: int, broadcast) -> None:
    """Live-Hook (pipeline.py, nach jedem abgeschlossenen Turn). Nur bei
    Rundenzahl exakt 2, 4 oder 8 — max. drei LLM-Aufrufe pro Thread über die
    gesamte Lebensdauer im laufenden Betrieb. Titel-Check zuerst (billiger
    PK-Lookup) spart die Rundenzählung (Tabellen-Scan) im Normalfall eines
    längst benannten Threads."""
    if session_memory.get_thread_title(thread_id) is not None:
        return
    if session_memory.count_rounds(thread_id) not in _ON_TURN_ROUNDS:
        return
    _attempt_naming(thread_id, broadcast)


def maybe_name_thread_once(thread_id: int, broadcast) -> None:
    """Startup-Sweep-Einstiegspunkt (server.py::main(), pro Thread genau ein
    Aufruf) — keine Obergrenze wie beim Live-Hook, da hier ohnehin nur einmal
    versucht wird, nicht wiederholt."""
    if session_memory.get_thread_title(thread_id) is not None:
        return
    if session_memory.count_rounds(thread_id) < _SWEEP_MIN_ROUNDS:
        return
    _attempt_naming(thread_id, broadcast)


def run_startup_sweep(broadcast) -> None:
    """Einmaliger Durchlauf beim Serverstart über ALLE bereits bestehenden
    unbenannten Threads — der Live-Hook läuft nur bei einem NEUEN Turn, ein
    Thread in dem nicht mehr geschrieben wird bekommt sonst nie einen Titel.
    Läuft in einem eigenen Hintergrund-Thread (siehe server.py::main()),
    NACHEINANDER statt parallel (gedrosselt — kleine Pause zwischen den
    Aufrufen, kein Burst gleichzeitiger API-Calls beim Start). Bewusst kein
    gemeinsames Rate-Limiting mit dem llm_semaphore der Chat-Pipeline — ein
    einzelner langsamer Benennungs-Call darf nie einen echten Chat blockieren
    und umgekehrt, llm.complete()s eigener 120s-Timeout bleibt die einzige
    Absicherung."""
    try:
        candidates = session_memory.list_unnamed_thread_ids()
        print(f"[thread_naming] Startup-Sweep: {len(candidates)} unbenannte Threads gefunden", flush=True)
        for thread_id in candidates:
            maybe_name_thread_once(thread_id, broadcast)
            time.sleep(_SWEEP_THROTTLE_SECONDS)
    except Exception as e:
        print(f"[thread_naming] Startup-Sweep-Fehler: {e}", flush=True)
