import queue
import customtkinter as ctk
from jarvis_engine import State, JarvisEngine
from gui.chat_view import ChatView
from gui.status_bar import StatusBar
from gui.tray_icon import TrayIcon

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class AppWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("J.A.R.V.I.S.")
        self.geometry("820x620")
        self.minsize(600, 400)
        self.configure(fg_color="#0d0d0d")

        self._events_out: queue.Queue = queue.Queue()
        self._commands_in: queue.Queue = queue.Queue()
        self._engine = JarvisEngine(self._events_out, self._commands_in)
        self._mode = "voice"

        self._build_ui()
        self._tray = TrayIcon(
            on_show_window=self._show_window,
            on_toggle_mode=self._toggle_mode,
            on_quit=self._quit,
            get_mode=lambda: self._mode,
        )
        self._tray.start()

        self._engine.start()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, height=48, fg_color="#161616", corner_radius=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="◆  J.A.R.V.I.S.", font=("SF Pro", 16, "bold"),
            text_color="#e0f2fe"
        ).pack(side="left", padx=16)

        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", padx=12)

        self._mode_btn = ctk.CTkButton(
            btn_frame, text="Voice", width=80, height=28,
            font=("SF Pro", 13), fg_color="#1e3a5f", hover_color="#2a4f7f",
            command=self._toggle_mode,
        )
        self._mode_btn.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="⚙", width=32, height=28,
            font=("SF Pro", 15), fg_color="#2a2a2a", hover_color="#3a3a3a",
            command=self._open_settings,
        ).pack(side="left")

        # Chat
        self._chat = ChatView(self)
        self._chat.pack(fill="both", expand=True, padx=0, pady=0)

        # Text-Input (Text-Mode only, hidden initially)
        self._input_frame = ctk.CTkFrame(self, height=52, fg_color="#161616", corner_radius=0)
        self._input_var = ctk.StringVar()
        self._input_field = ctk.CTkEntry(
            self._input_frame, textvariable=self._input_var,
            placeholder_text="Nachricht eingeben…",
            font=("SF Pro", 14), height=36, fg_color="#2a2a2a", border_width=0,
        )
        self._input_field.pack(side="left", fill="x", expand=True, padx=(12, 8), pady=8)
        self._input_field.bind("<Return>", self._send_text)

        ctk.CTkButton(
            self._input_frame, text="→", width=36, height=36,
            font=("SF Pro", 16), fg_color="#1e3a5f", hover_color="#2a4f7f",
            command=self._send_text,
        ).pack(side="right", padx=(0, 12), pady=8)

        # Status bar
        self._status = StatusBar(self)
        self._status.pack(fill="x", side="bottom")

    # ── Event Polling ─────────────────────────────────────────────────────────

    def _poll_events(self):
        try:
            while True:
                event_type, data = self._events_out.get_nowait()
                self._handle_event(event_type, data)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _handle_event(self, event_type: str, data):
        if event_type == "state":
            self._status.set_state(data)
        elif event_type == "status_text":
            self._status.set_custom_text(data)
        elif event_type == "user_text":
            self._chat.add_user_message(data)
        elif event_type == "response_start":
            self._chat.start_jarvis_message()
        elif event_type == "response_chunk":
            self._chat.append_to_jarvis_message(data)
        elif event_type == "response_done":
            self._chat.finalize_jarvis_message()
        elif event_type == "tool_running":
            self._status.set_tool(data)
        elif event_type == "error":
            self._chat.add_error(data)
        elif event_type == "mode_changed":
            self._mode = data
            self._update_mode_ui()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _toggle_mode(self):
        new_mode = "text" if self._mode == "voice" else "voice"
        self._mode = new_mode
        self._engine.set_mode(new_mode)
        self._update_mode_ui()

    def _update_mode_ui(self):
        if self._mode == "voice":
            self._mode_btn.configure(text="Voice")
            self._input_frame.pack_forget()
        else:
            self._mode_btn.configure(text="Text")
            self._input_frame.pack(fill="x", side="bottom", before=self._status)
            self._input_field.focus()

    def _send_text(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        self._input_var.set("")
        self._commands_in.put(("text_input", text))

    def _open_settings(self):
        from gui.settings_view import SettingsWindow
        win = SettingsWindow(self)
        win.grab_set()

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self):
        self.withdraw()

    def _quit(self):
        self._engine.stop()
        self._tray.stop()
        self.destroy()
