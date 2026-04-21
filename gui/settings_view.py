import plistlib
import subprocess
import sounddevice as sd
from pathlib import Path
from dotenv import dotenv_values, set_key
import customtkinter as ctk

ENV_PATH = Path(__file__).parent.parent / ".env"
PLIST_PATH = Path.home() / "Library/LaunchAgents/com.simonfischer.jarvis.plist"
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]


def _get_audio_devices() -> list[tuple[int, str]]:
    devices = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append((i, dev["name"]))
    except Exception:
        pass
    return devices


def _is_autostart_enabled() -> bool:
    return PLIST_PATH.exists()


def _enable_autostart():
    import sys
    app_path = sys.executable
    content = plistlib.dumps({
        "Label": "com.simonfischer.jarvis",
        "ProgramArguments": [app_path, str(Path(__file__).parent.parent / "app.py")],
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(Path.home() / ".jarvis/jarvis.log"),
        "StandardErrorPath": str(Path.home() / ".jarvis/jarvis_err.log"),
    })
    PLIST_PATH.write_bytes(content)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True)


def _disable_autostart():
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        PLIST_PATH.unlink()


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Einstellungen")
        self.geometry("560x620")
        self.resizable(False, False)
        self.configure(fg_color="#111111")

        self._env = dotenv_values(ENV_PATH)
        self._widgets: dict = {}
        self._build()

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="#111111")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        self._add_section(scroll, "Audio")
        self._add_dropdown(scroll, "Whisper-Modell", "WHISPER_MODEL", WHISPER_MODELS)
        devices = _get_audio_devices()
        device_labels = ["(Standard)"] + [f"{i}: {n}" for i, n in devices]
        self._add_dropdown(scroll, "Mikrofon", "AUDIO_INPUT_DEVICE", device_labels)

        self._add_section(scroll, "Wake Word & Modus")
        self._add_switch(scroll, "Wake Word aktiviert (= kein MANUAL_MODE)", "MANUAL_MODE", invert=True)

        self._add_section(scroll, "Text-to-Speech")
        self._add_entry(scroll, "ElevenLabs Voice ID", "ELEVENLABS_VOICE_ID")

        self._add_section(scroll, "Allgemein")
        self._add_entry(scroll, "Wetter-Stadt", "WEATHER_CITY")
        self._add_autostart(scroll)

        self._add_section(scroll, "API Keys")
        for label, key in [
            ("Anthropic API Key", "ANTHROPIC_API_KEY"),
            ("ElevenLabs API Key", "ELEVENLABS_API_KEY"),
            ("Notion API Key", "NOTION_API_KEY"),
            ("GitHub Token", "GITHUB_TOKEN"),
            ("Email-Adresse", "EMAIL_ADDRESS"),
        ]:
            self._add_entry(scroll, label, key, secret=True)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="#1a1a1a", height=56, corner_radius=0)
        btn_frame.pack(fill="x", side="bottom")
        btn_frame.pack_propagate(False)

        ctk.CTkButton(
            btn_frame, text="Abbrechen", width=100, fg_color="#2a2a2a", hover_color="#3a3a3a",
            command=self.destroy,
        ).pack(side="left", padx=12, pady=10)

        ctk.CTkButton(
            btn_frame, text="Speichern", width=100, fg_color="#1e3a5f", hover_color="#2a4f7f",
            command=self._save,
        ).pack(side="right", padx=12, pady=10)

    def _add_section(self, parent, title: str):
        ctk.CTkLabel(
            parent, text=title.upper(), font=("SF Pro", 11, "bold"),
            text_color="#555555", anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkFrame(parent, height=1, fg_color="#2a2a2a").pack(fill="x", padx=16, pady=(0, 8))

    def _add_entry(self, parent, label: str, env_key: str, secret: bool = False):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row, text=label, font=("SF Pro", 13), width=200, anchor="w").pack(side="left")
        var = ctk.StringVar(value=self._env.get(env_key, ""))
        entry = ctk.CTkEntry(
            row, textvariable=var, font=("SF Pro", 13),
            show="•" if secret else "", fg_color="#1e1e1e", border_width=1, border_color="#333",
        )
        entry.pack(side="right", fill="x", expand=True)
        self._widgets[env_key] = ("entry", var)

    def _add_dropdown(self, parent, label: str, env_key: str, options: list):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row, text=label, font=("SF Pro", 13), width=200, anchor="w").pack(side="left")
        current = self._env.get(env_key, options[0] if options else "")
        var = ctk.StringVar(value=current)
        ctk.CTkOptionMenu(row, variable=var, values=options, font=("SF Pro", 13)).pack(side="right")
        self._widgets[env_key] = ("dropdown", var)

    def _add_switch(self, parent, label: str, env_key: str, invert: bool = False):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row, text=label, font=("SF Pro", 13), anchor="w").pack(side="left", fill="x", expand=True)
        raw = self._env.get(env_key, "false").lower() == "true"
        value = (not raw) if invert else raw
        var = ctk.BooleanVar(value=value)
        ctk.CTkSwitch(row, text="", variable=var, width=46).pack(side="right")
        self._widgets[env_key] = ("switch_inverted" if invert else "switch", var)

    def _add_autostart(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row, text="Autostart beim Login", font=("SF Pro", 13), anchor="w").pack(side="left", fill="x", expand=True)
        var = ctk.BooleanVar(value=_is_autostart_enabled())
        ctk.CTkSwitch(row, text="", variable=var, width=46).pack(side="right")
        self._widgets["__autostart__"] = ("autostart", var)

    def _save(self):
        for env_key, (kind, var) in self._widgets.items():
            if env_key == "__autostart__":
                if var.get():
                    _enable_autostart()
                else:
                    _disable_autostart()
                continue

            if kind == "entry":
                value = var.get()
            elif kind == "dropdown":
                raw = var.get()
                value = raw.split(":")[0].strip() if raw != "(Standard)" else ""
            elif kind == "switch":
                value = "true" if var.get() else "false"
            elif kind == "switch_inverted":
                value = "false" if var.get() else "true"
            else:
                continue

            set_key(str(ENV_PATH), env_key, value)

        self.destroy()
