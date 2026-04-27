import shutil
import threading
from pathlib import Path
from tkinter import filedialog
import customtkinter as ctk
from dotenv import set_key, dotenv_values

ENV_PATH = Path(__file__).parent.parent / ".env"
JARVIS_DIR = Path.home() / ".jarvis"


def _ensure_env():
    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            "ANTHROPIC_API_KEY=\nELEVENLABS_API_KEY=\nELEVENLABS_VOICE_ID=\n"
            "NOTION_API_KEY=\nEMAIL_ADDRESS=\nWEATHER_CITY=München\n"
            "WHISPER_MODEL=base\nAUDIO_INPUT_DEVICE=\nMANUAL_MODE=false\n"
        )


def needs_setup() -> bool:
    env = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return not env.get("ANTHROPIC_API_KEY", "").strip()


class SetupWizard(ctk.CTkToplevel):
    STEPS = ["Willkommen", "API Keys", "Notion", "Google Calendar", "E-Mail", "Fertig"]

    def __init__(self, master, on_complete):
        super().__init__(master)
        self.title("J.A.R.V.I.S. Einrichtung")
        self.geometry("560x520")
        self.resizable(False, False)
        self.configure(fg_color="#0d0d0d")
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # Nicht schließbar

        self._on_complete = on_complete
        self._step = 0
        self._widgets: dict = {}
        self._gcal_status = ctk.StringVar(value=self._gcal_current_status())

        _ensure_env()
        self._env = dict(dotenv_values(ENV_PATH))

        self._header = ctk.CTkLabel(
            self, text="", font=("SF Pro", 13), text_color="#555555"
        )
        self._header.pack(pady=(20, 0))

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=32, pady=16)

        self._nav = ctk.CTkFrame(self, fg_color="#111111", height=60, corner_radius=0)
        self._nav.pack(fill="x", side="bottom")
        self._nav.pack_propagate(False)

        self._btn_back = ctk.CTkButton(
            self._nav, text="← Zurück", width=100, fg_color="#2a2a2a", hover_color="#3a3a3a",
            command=self._back,
        )
        self._btn_back.pack(side="left", padx=16, pady=12)

        self._btn_skip = ctk.CTkButton(
            self._nav, text="Überspringen", width=110, fg_color="transparent",
            hover_color="#1a1a1a", border_width=1, border_color="#333",
            command=self._skip,
        )
        self._btn_skip.pack(side="left", padx=0, pady=12)

        self._btn_next = ctk.CTkButton(
            self._nav, text="Weiter →", width=110, fg_color="#1e3a5f", hover_color="#2a4f7f",
            command=self._next,
        )
        self._btn_next.pack(side="right", padx=16, pady=12)

        self._render()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _next(self):
        self._save_current()
        self._step += 1
        self._render()

    def _back(self):
        self._save_current()
        self._step -= 1
        self._render()

    def _skip(self):
        self._step += 1
        self._render()

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self):
        for w in self._content.winfo_children():
            w.destroy()
        self._widgets.clear()

        total = len(self.STEPS)
        step_name = self.STEPS[self._step]
        self._header.configure(text=f"Schritt {self._step + 1} von {total}  —  {step_name}")

        self._btn_back.configure(state="normal" if self._step > 0 else "disabled")
        is_last = self._step == total - 1
        is_first = self._step == 0
        self._btn_skip.configure(state="normal" if not is_first and not is_last else "disabled")
        self._btn_next.configure(
            text="JARVIS starten" if is_last else "Weiter →",
            command=self._finish if is_last else self._next,
        )

        getattr(self, f"_step_{self._step}")()

    def _finish(self):
        self._save_current()
        self.grab_release()
        self.destroy()
        self._on_complete()

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _step_0(self):
        ctk.CTkLabel(
            self._content, text="◆  J.A.R.V.I.S.",
            font=("SF Pro", 28, "bold"), text_color="#e0f2fe",
        ).pack(pady=(30, 8))
        ctk.CTkLabel(
            self._content,
            text="Dein persönlicher KI-Assistent.\nEinmalig einrichten — dann läuft alles.",
            font=("SF Pro", 14), text_color="#888888", justify="center",
        ).pack(pady=(0, 24))
        ctk.CTkLabel(
            self._content,
            text="Du kannst jeden Schritt überspringen\nund später in den Einstellungen nachtragen.",
            font=("SF Pro", 12), text_color="#555555", justify="center",
        ).pack()

    def _step_1(self):
        self._section("Pflicht — JARVIS kann ohne diese Keys nicht starten")
        self._entry("Anthropic API Key", "ANTHROPIC_API_KEY", secret=True)
        self._entry("ElevenLabs API Key", "ELEVENLABS_API_KEY", secret=True)
        self._entry("ElevenLabs Voice ID", "ELEVENLABS_VOICE_ID")

    def _step_2(self):
        self._section("Optional — für Todos, Projekte und Konzepte")
        self._entry("Notion API Key", "NOTION_API_KEY", secret=True)

    def _step_3(self):
        self._section("Optional — Kalender lesen und schreiben")

        status = self._gcal_status.get()
        color = "#22c55e" if "Verbunden" in status else "#888888"
        ctk.CTkLabel(
            self._content, textvariable=self._gcal_status,
            font=("SF Pro", 12), text_color=color,
        ).pack(anchor="w", pady=(0, 12))

        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", pady=4)

        ctk.CTkButton(
            row, text="credentials.json auswählen", width=200,
            fg_color="#2a2a2a", hover_color="#3a3a3a",
            command=self._pick_gcal_credentials,
        ).pack(side="left")

        self._btn_gcal_connect = ctk.CTkButton(
            row, text="Verbinden", width=100,
            fg_color="#1e3a5f", hover_color="#2a4f7f",
            command=self._connect_gcal,
            state="normal" if (JARVIS_DIR / "google_credentials.json").exists() else "disabled",
        )
        self._btn_gcal_connect.pack(side="left", padx=12)

        ctk.CTkLabel(
            self._content,
            text="Credentials-JSON: Google Cloud Console →\nAPIs & Dienste → Anmeldedaten → OAuth 2.0-Client-ID (Desktop App)",
            font=("SF Pro", 11), text_color="#444444", justify="left",
        ).pack(anchor="w", pady=(16, 0))

    def _step_4(self):
        self._section("Optional — E-Mails lesen und schreiben")
        self._entry("E-Mail Adresse", "EMAIL_ADDRESS")

    def _step_5(self):
        ctk.CTkLabel(
            self._content, text="Alles erledigt!",
            font=("SF Pro", 22, "bold"), text_color="#e0f2fe",
        ).pack(pady=(30, 12))
        env = dict(dotenv_values(ENV_PATH))
        lines = []
        if env.get("ANTHROPIC_API_KEY"):
            lines.append("✓  Anthropic API Key")
        if env.get("ELEVENLABS_API_KEY"):
            lines.append("✓  ElevenLabs API Key")
        if env.get("NOTION_API_KEY"):
            lines.append("✓  Notion")
        if (JARVIS_DIR / "google_token.json").exists():
            lines.append("✓  Google Calendar")
        if env.get("EMAIL_ADDRESS"):
            lines.append("✓  E-Mail")
        if not lines:
            lines.append("⚠  Noch keine Keys hinterlegt — in den Einstellungen nachtragen")
        for line in lines:
            ctk.CTkLabel(
                self._content, text=line,
                font=("SF Pro", 13), text_color="#888888", anchor="w",
            ).pack(anchor="w", padx=32, pady=2)

    # ── Google Calendar ───────────────────────────────────────────────────────

    def _gcal_current_status(self) -> str:
        if (JARVIS_DIR / "google_token.json").exists():
            return "● Verbunden"
        if (JARVIS_DIR / "google_credentials.json").exists():
            return "○ Credentials vorhanden – noch nicht verbunden"
        return "○ Nicht eingerichtet"

    def _pick_gcal_credentials(self):
        path = filedialog.askopenfilename(
            title="Google Credentials JSON auswählen",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        JARVIS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, JARVIS_DIR / "google_credentials.json")
        self._gcal_status.set("○ Credentials gespeichert – jetzt verbinden")
        if hasattr(self, "_btn_gcal_connect"):
            self._btn_gcal_connect.configure(state="normal")

    def _connect_gcal(self):
        self._gcal_status.set("⏳ Browser öffnet sich…")
        if hasattr(self, "_btn_gcal_connect"):
            self._btn_gcal_connect.configure(state="disabled", text="Verbinde…")

        def _run():
            try:
                import google_auth
                google_auth.get_credentials()
                self.after(0, lambda: self._gcal_status.set("● Verbunden"))
            except Exception as e:
                self.after(0, lambda: self._gcal_status.set(f"✗ Fehler: {e}"))
            finally:
                if hasattr(self, "_btn_gcal_connect"):
                    self.after(0, lambda: self._btn_gcal_connect.configure(
                        state="normal", text="Verbinden"
                    ))

        threading.Thread(target=_run, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, text: str):
        ctk.CTkLabel(
            self._content, text=text,
            font=("SF Pro", 11), text_color="#555555", anchor="w",
        ).pack(fill="x", pady=(0, 12))

    def _entry(self, label: str, env_key: str, secret: bool = False):
        row = ctk.CTkFrame(self._content, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text=label, font=("SF Pro", 13), width=180, anchor="w").pack(side="left")
        var = ctk.StringVar(value=self._env.get(env_key, ""))
        ctk.CTkEntry(
            row, textvariable=var, font=("SF Pro", 13),
            show="•" if secret else "", fg_color="#1e1e1e",
            border_width=1, border_color="#333",
        ).pack(side="right", fill="x", expand=True)
        self._widgets[env_key] = var

    def _save_current(self):
        for env_key, var in self._widgets.items():
            value = var.get().strip()
            if value:
                set_key(str(ENV_PATH), env_key, value)
        self._env = dict(dotenv_values(ENV_PATH))
