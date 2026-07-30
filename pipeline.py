"""
JarvisPipeline — Herzstück des JARVIS-Systems.
Kapselt STT → LLM → TTS. Unabhängig von Transport (WebSocket, lokal, Text).
Eine Instanz pro Client.
"""
from __future__ import annotations
import os
import queue
import re
import tempfile
import threading
import time

import anthropic

import context
import llm
import protocol as P
import session_memory
import stt
import tools
import tracking
import tts

SENTENCE_END = re.compile(r'([^.!?\n]{15,}[.!?\n]+)')
_STRIP_PARENS = re.compile(r'\([^)]*\)')
_NON_ALPHA = re.compile(r'[^\w]', re.UNICODE)
_MIN_MEANINGFUL = 3
TTS_BUFFER_MIN = 120


def _is_noise(text: str) -> bool:
    """True wenn text keine bedeutsame Sprache enthält (nur Geräuschbeschreibungen, Kurzfüller etc.)"""
    if not text:
        return True
    cleaned = _STRIP_PARENS.sub('', text).strip()
    return len(_NON_ALPHA.sub('', cleaned)) < _MIN_MEANINGFUL


_TEXT_MIME_PREFIXES = ("text/",)
_CSV_MIME_TYPES = ("text/csv", "application/vnd.ms-excel")


def _attachment_to_block(att: dict) -> dict:
    """Wandelt einen Chat-Anhang (jarvis-web) in einen Claude-Content-Block um.
    Bilder/PDFs gehen als base64 direkt an die API; Text-Dateien werden serverseitig
    dekodiert und inline als Text eingefügt (robuster als sich auf Claudes
    Text-Dokument-Unterstützung zu verlassen).
    CSV-Dateien sind ein Sonderfall: statt roh als Text inline zu gehen (Claude müsste
    dann selbst parsen/upserten, fehleranfällig und teuer), läuft hier direkt der
    deterministische SevDesk-Import (finanzen_import.detect_and_import) — Claude
    bekommt nur die fertige Zusammenfassung und kann bei fehlender Projekt-Zuordnung
    gezielt nachfragen (siehe data_query/data_update auf 'rechnungen')."""
    import base64
    filename = att.get("filename", "Datei")
    mime_type = att.get("mime_type", "application/octet-stream")
    data_b64 = att.get("data_base64", "")

    if mime_type in _CSV_MIME_TYPES or filename.lower().endswith(".csv"):
        try:
            text = base64.b64decode(data_b64).decode("utf-8-sig", errors="replace")
            import finanzen_import
            result = finanzen_import.detect_and_import(text)
            kind_label = "Rechnungen" if result["kind"] == "rechnungen" else "Ausgaben"
            summary = (
                f"[CSV-Import: {filename}] Erkannt als {kind_label}-Export: "
                f"{result['created']} neu, {result['updated']} aktualisiert (von {result['total']})."
            )
            if result.get("skipped_locked"):
                summary += f" {result['skipped_locked']} gesperrte Einträge unangetastet gelassen."
            unmatched = result.get("unmatched") or []
            if unmatched:
                preview = ", ".join(
                    f"{u['rechnungsnummer']} ({u['kunde']}, {u['betrag_netto']}€)" for u in unmatched[:5]
                )
                summary += (
                    f" {len(unmatched)} Rechnungen ohne Projekt-Zuordnung, z.B.: {preview}. "
                    "Frag Simon aktiv, welchem Projekt diese zuzuordnen sind, und setze "
                    "projekt_id anschließend über data_update (nie automatisch raten)."
                )
        except Exception as e:
            summary = f"[CSV-Import von {filename} fehlgeschlagen: {e}]"
        return {"type": "text", "text": summary}
    if mime_type.startswith("image/"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": data_b64}}
    if mime_type == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": mime_type, "data": data_b64}}
    if mime_type.startswith(_TEXT_MIME_PREFIXES):
        try:
            text = base64.b64decode(data_b64).decode("utf-8", errors="replace")
        except Exception:
            text = "(Datei konnte nicht gelesen werden)"
        return {"type": "text", "text": f"[Datei: {filename}]\n{text}"}
    return {"type": "text", "text": f"[Datei: {filename} — Dateityp {mime_type!r} wird nicht unterstützt]"}


class JarvisPipeline:
    def __init__(self, client_id: str, on_event, on_audio=None,
                 shared_history: list | None = None,
                 history_lock: threading.Lock | None = None,
                 llm_semaphore: threading.Semaphore | None = None,
                 room: str | None = None):
        """
        client_id      : eindeutiger Name des Clients
        on_event       : callable(event: dict)
        on_audio       : callable(pcm: bytes) | None
        shared_history : geteilte History aller Clients (vom Server übergeben)
        history_lock   : Lock für thread-sicheren History-Zugriff
        llm_semaphore  : stellt sicher dass nur ein LLM-Call gleichzeitig läuft (FIFO)
        room           : Raumname für Dynamic Prompt (= Client-Name)
        """
        self.client_id = client_id
        self._on_event = on_event
        self._on_audio = on_audio
        self.history: list[dict] = shared_history if shared_history is not None else []
        self._history_lock = history_lock or threading.Lock()
        self._llm_semaphore = llm_semaphore or threading.Semaphore(1)
        self._room = room or client_id
        self._mode = "assistent"
        # Idempotenz: tool_use_ids die bereits in dieser Session ausgeführt wurden
        self._executed_tool_ids: dict[str, str] = {}  # call_id → result
        # Context-Module: beim ersten Turn erkannt, für die Session gespeichert
        self._active_modules: set[str] | None = None
        # Adaptive Thinking — per Client/Session umschaltbar (z.B. an im Web-Chat wo
        # Latenz egal ist, aus bei Voice wo STT→LLM→TTS schnell bleiben muss). Default
        # aus, damit sich das Antwortverhalten beim Sonnet-5-Umstieg nicht stillschweigend
        # ändert (Sonnet 5 denkt sonst per Default, anders als das bisherige Sonnet 4.6).
        self._thinking_enabled: bool = False
        # LLM-Modell — per Client/Session wählbar (siehe llm.MODEL_CATALOG), Default Sonnet 5.
        self._model: str = llm.MODEL
        # Herkunft dieses Turns (category/tab_id, siehe server.py) — an
        # tools.execute() durchgereicht, damit z.B. start_coding_job weiß, in
        # welchem Tab ein späteres Jobergebnis als Chat-Nachricht landen soll
        # (siehe services/coding_jobs.py::start_job). None bis set_chat_target()
        # aufgerufen wird (server.py, direkt nach der Konstruktion).
        self._category: str | None = None
        self._tab_id: str | None = None

    def set_room(self, room: str):
        self._room = room

    def set_chat_target(self, category: str, tab_id: str):
        self._category = category
        self._tab_id = tab_id

    def set_mode(self, mode: str):
        self._mode = mode
        self._active_modules = None  # Neue Session bei Modus-Wechsel

    def set_thinking(self, enabled: bool):
        self._thinking_enabled = bool(enabled)

    def set_model(self, model: str):
        if model in llm.MODEL_CATALOG:
            self._model = model

    def greet(self, text: str = "Bereit."):
        """Begrüßung beim Connect eines Sprach-Clients — bewusst OHNE LLM-Call.
        Reiner Verbindungs-Bestätigungston, braucht keine Intelligenz. Über die
        normale Pipeline gejagt hätte das den vollen System-Prompt + alle
        Tool-Definitionen gekostet — bei kaltem/abgelaufenem Cache (z.B. direkt
        nach einem Server-Neustart) ein voller Cache-Neuschreib nur für ein Wort.
        Fasst History nicht an (war ohnehin nie 'echter' Gesprächsinhalt)."""
        self._emit(P.RESPONSE_START)
        self._emit(P.RESPONSE_CHUNK, text=text)
        self._emit(P.RESPONSE_DONE, text=text)

        if self._on_audio:
            self._emit(P.STATE, state="speaking")
            tts_queue: queue.Queue = queue.Queue()
            tts_done = threading.Event()
            threading.Thread(
                target=tts.speak_response,
                args=(tts_queue, tts_done, self._on_audio),
                daemon=True,
            ).start()
            tts_queue.put(text)
            tts_queue.put(None)
            tts_done.wait()

        self._emit(P.STATE, state="idle")

    # ── Öffentliche Methoden ──────────────────────────────────────────────────

    def process_audio(self, wav_bytes: bytes):
        """WAV-Bytes → STT → process_text()"""
        t_audio_recv = time.monotonic()
        print(f"[pipeline] Audio empfangen: {len(wav_bytes)/1024:.1f}KB", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name

        self._emit(P.STATUS, text="Transkribiere…")
        user_text = stt.transcribe(wav_path)
        t_stt_done = time.monotonic()
        print(f"[pipeline] STT fertig: {t_stt_done - t_audio_recv:.2f}s gesamt seit Empfang", flush=True)
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        if _is_noise(user_text):
            self._emit(P.STATE, state="idle")
            return

        self._emit(P.TRANSCRIPT, text=user_text)
        self.process_text(user_text, use_tts=self._on_audio is not None)

    def process_text(self, text: str, use_tts: bool = True, attachments: list[dict] | None = None):
        """Text (+ optionale Datei-Anhänge) → LLM → (TTS) → Events. Semaphore stellt FIFO sicher (kein paralleler LLM-Call)."""
        with self._llm_semaphore:
            if attachments:
                content = ([{"type": "text", "text": text}] if text else []) + [
                    _attachment_to_block(a) for a in attachments
                ]
            else:
                content = text
            with self._history_lock:
                self.history.append({"role": "user", "content": content})
                history_snapshot = list(self.history)

            # Beim ersten Turn der Session: Module erkennen
            if self._active_modules is None:
                self._active_modules = context.detect_modules(text, self._mode)

            system_static = context.build_static_prompt(mode=self._mode, active_modules=self._active_modules)
            system_dynamic = context.build_dynamic_prompt(room=self._room, active_modules=self._active_modules)

            self._emit(P.STATE, state="thinking")
            response, final_messages, turn_cost = self._run_llm(system_static, system_dynamic, history_snapshot, use_tts=use_tts)

            with self._history_lock:
                if final_messages:
                    # Vollständige History inkl. Tool-Calls sichern — verhindert Doppel-Reads
                    # Alte Anhänge (Bilder/PDFs) komprimieren — sonst wird bei jedem
                    # weiteren Turn wieder das komplette Base64 mitgeschickt.
                    final_messages = llm.compress_attachment_history(final_messages)
                    # Der jeweils LETZTE Tool-Result eines Turns bleibt in _run_llm()s
                    # eigenem compress_tool_history()-Aufruf bewusst unkomprimiert (der
                    # kommt erst beim NÄCHSTEN Tool-Aufruf dran) — ohne diesen zusätzlichen
                    # Aufruf hier würde er dauerhaft unkomprimiert in self.history hängen
                    # bleiben, bei tool-lastigen Gesprächen zieht das den Cap (unten)
                    # deutlich früher als nötig (2026-07-22: nach einem Neustart fehlte ein
                    # großer Mittelteil eines Tauri-Debugging-Gesprächs — Ursache war genau
                    # diese Kombination aus hartem Entry-Cap + unkomprimierten Tool-Results).
                    final_messages = llm.compress_tool_history(final_messages)
                    del self.history[:]
                    self.history.extend(final_messages)
                    # Cap deutlich höher als die alten 40 — dank der Kompression oben kostet
                    # ein einzelner Eintrag jetzt viel weniger, es passen also spürbar mehr
                    # echte Gesprächs-Turns rein bevor überhaupt getrimmt werden muss.
                    if len(self.history) > 150:
                        del self.history[:-150]
                elif response:
                    self.history.append({"role": "assistant", "content": response})
                    if len(self.history) > 40:
                        del self.history[:-40]
                else:
                    # _run_llm ist komplett gescheitert (Timeout/API-Fehler/Exception,
                    # kein Response-Text) — die oben angehängte User-Nachricht muss
                    # wieder raus. Sonst hätte der NÄCHSTE Turn zwei aufeinanderfolgende
                    # "user"-Rollen in der History, was die Anthropic-API generell mit
                    # "roles must alternate" ablehnt — EIN Timeout hätte damit die
                    # komplette restliche Session unbrauchbar gemacht, jede weitere
                    # Nachricht wäre aus genau demselben Grund erneut gescheitert
                    # (2026-07-22 live beobachtet: "ich laufe immer wieder in diesen
                    # Timeout, jarvis antwortet dann einfach nicht mehr").
                    if self.history and self.history[-1].get("role") == "user":
                        self.history.pop()

            if turn_cost:
                try:
                    tracking.add_log("chat", key="cost_usd", value=round(turn_cost, 6), unit="usd")
                except Exception as e:
                    print(f"[pipeline] Kosten-Log Fehler: {e}", flush=True)

            self._emit(P.STATE, state="idle")

    def save_session(self):
        with self._history_lock:
            snapshot = list(self.history)
        if snapshot:
            t = session_memory.save(snapshot)
            if t:
                t.join(timeout=10)

    # ── LLM-Loop ─────────────────────────────────────────────────────────────

    def _run_llm(
        self,
        system_static: str,
        system_dynamic: str,
        messages: list[dict],
        use_tts: bool = True,
    ) -> tuple[str, list[dict], float]:
        client_messages = messages.copy()
        full_response = ""
        total_cost = 0.0

        while True:
            tts_queue: queue.Queue = queue.Queue()
            turn_text = ""
            buffer = ""
            tts_done = threading.Event()

            if use_tts and self._on_audio:
                threading.Thread(
                    target=tts.speak_response,
                    args=(tts_queue, tts_done, self._on_audio),
                    daemon=True,
                ).start()
            else:
                tts_done.set()

            response_started = False
            first_chunk_sent = False
            t_llm_start = time.monotonic()
            t_first_token = None

            try:
                with llm.stream(system_static, system_dynamic, client_messages, tools.DEFINITIONS,
                                 thinking=self._thinking_enabled, model=self._model) as s:
                    for chunk in s.text_stream:
                        if not response_started:
                            self._emit(P.RESPONSE_START)
                            response_started = True
                            t_first_token = time.monotonic()
                            print(f"[pipeline] LLM erstes Token: {t_first_token - t_llm_start:.2f}s", flush=True)

                        buffer += chunk
                        turn_text += chunk
                        self._emit(P.RESPONSE_CHUNK, text=chunk)

                        if use_tts and self._on_audio:
                            while True:
                                match = SENTENCE_END.search(buffer)
                                if match and match.end() >= TTS_BUFFER_MIN:
                                    send = buffer[:match.end()].strip()
                                    buffer = buffer[match.end():].lstrip()
                                    if send:
                                        if not first_chunk_sent:
                                            first_chunk_sent = True
                                            self._emit(P.STATE, state="speaking")
                                            print(f"[pipeline] TTS erster Satz gesendet: {time.monotonic() - t_llm_start:.2f}s seit LLM-Start", flush=True)
                                        tts_queue.put(send)
                                else:
                                    break

                    final = s.get_final_message()
                    total_cost += llm.compute_cost(final.usage, model=self._model)
                    print(f"[pipeline] LLM fertig: {time.monotonic() - t_llm_start:.2f}s, stop={final.stop_reason}", flush=True)

            except anthropic.APIStatusError as e:
                if use_tts and self._on_audio:
                    tts_queue.put(None)
                if "overloaded" in str(e).lower():
                    print(f"[pipeline] Anthropic überlastet nach {time.monotonic() - t_llm_start:.2f}s: {e}", flush=True)
                    self._emit(P.ERROR, message="Anthropic überlastet.")
                    time.sleep(5)
                else:
                    print(f"[pipeline] API-Fehler nach {time.monotonic() - t_llm_start:.2f}s: {e}", flush=True)
                    self._emit(P.ERROR, message=f"API-Fehler: {e}")
                return "", [], total_cost
            except Exception as e:
                if use_tts and self._on_audio:
                    tts_queue.put(None)
                print(f"[pipeline] Unerwarteter Fehler nach {time.monotonic() - t_llm_start:.2f}s: {e}", flush=True)
                self._emit(P.ERROR, message=f"Fehler: {e}")
                return "", [], total_cost

            if use_tts and self._on_audio:
                if buffer.strip():
                    tts_queue.put(buffer.strip())
                tts_queue.put(None)

            tts_done.wait()

            # Sicherheits-Ablehnung (stop_reason="refusal" — v.a. bei Fable 5 möglich,
            # dessen Klassifikatoren u.a. auf Cyber-/Bio-nahe Themen ansprechen, aber
            # grundsätzlich auf jedem Modell mit den neueren Sicherheitsfiltern denkbar).
            # Ohne Sonderbehandlung wäre das weder "end_turn" noch "tool_use" — die
            # Schleife würde mit unveränderten client_messages einfach erneut denselben
            # Request stellen, vermutlich erneut mit derselben Ablehnung: Endlosschleife.
            if final.stop_reason == "refusal" and not turn_text.strip():
                turn_text = "Diese Anfrage wurde aus Sicherheitsgründen abgelehnt."

            # stop_reason="max_tokens": Antwort wurde mitten im Generieren abgeschnitten,
            # weil das Output-Limit erreicht wurde. War bisher UNBEHANDELT — weder
            # "end_turn"/"tool_use" noch "refusal" griffen, die Schleife oben lief mit
            # UNVERÄNDERTEN client_messages einfach erneut los: derselbe Request nochmal,
            # ohne dass die (verworfene) Antwort je in client_messages/self.history
            # landete. Live beobachtet (2026-07-28): eine Kaskade mehrerer ~75s-Calls,
            # jedes Mal erneut bei max_tokens abgeschnitten — JARVIS "vergaß" bei jedem
            # Versuch, dass es die Frage gerade schon erfolglos beantwortet hatte (der
            # gescheiterte Versuch stand ja nirgends), und wiederholte de facto denselben
            # Denkweg identisch, potenziell endlos. Jetzt wie "refusal" behandelt: die
            # abgeschnittene Antwort wird trotzdem an die History angehängt und der Turn
            # sauber beendet statt verworfen und blind wiederholt — ein Hinweis macht die
            # Kürzung für Simon UND für JARVIS selbst im nächsten Turn sichtbar.
            if final.stop_reason == "max_tokens":
                turn_text = (turn_text or "") + "\n\n*(Antwort abgeschnitten — Token-Limit erreicht.)*"

            self._emit(P.RESPONSE_DONE, text=turn_text)

            if final.stop_reason in ("end_turn", "refusal", "max_tokens"):
                full_response = turn_text
                if turn_text:
                    client_messages = client_messages + [{"role": "assistant", "content": turn_text}]
                break

            if final.stop_reason == "tool_use":
                client_messages = client_messages + [{"role": "assistant", "content": final.content}]
                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        self._emit(P.STATE, state="tool_running")
                        self._emit(P.TOOL, name=block.name)
                        call_id = block.id
                        if call_id in self._executed_tool_ids:
                            # Duplikat-Call in dieser Session — gecachtes Ergebnis zurückgeben
                            result = self._executed_tool_ids[call_id]
                            print(f"[pipeline] Tool-Dedup: {block.name} ({call_id})", flush=True)
                        else:
                            result = tools.execute(block.name, block.input, emit=self._emit, category=self._category, tab_id=self._tab_id)
                            self._executed_tool_ids[call_id] = result
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": result,
                        })
                client_messages = client_messages + [{"role": "user", "content": tool_results}]
                client_messages = llm.compress_tool_history(client_messages)
                self._emit(P.STATE, state="thinking")

        return full_response, client_messages, total_cost

    # ── Intern ────────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, **kwargs):
        self._on_event({"type": event_type, **kwargs})
