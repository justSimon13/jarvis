import customtkinter as ctk
from jarvis_engine import State

STATE_CONFIG = {
    State.IDLE:         ("●", "#555555", "Bereit"),
    State.LISTENING:    ("●", "#22c55e", "Hört zu"),
    State.THINKING:     ("●", "#eab308", "Denkt…"),
    State.SPEAKING:     ("●", "#3b82f6", "Spricht"),
    State.TOOL_RUNNING: ("●", "#f97316", "Tool läuft"),
}


class StatusBar(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, height=36, fg_color="#1a1a1a", **kwargs)
        self.grid_propagate(False)

        self._dot = ctk.CTkLabel(self, text="●", font=("SF Pro", 14), text_color="#555555", width=20)
        self._dot.pack(side="left", padx=(12, 4))

        self._label = ctk.CTkLabel(self, text="Bereit", font=("SF Pro", 13), text_color="#888888")
        self._label.pack(side="left")

        self._tool_label = ctk.CTkLabel(self, text="", font=("SF Pro", 12), text_color="#f97316")
        self._tool_label.pack(side="left", padx=(8, 0))

    def set_state(self, state: State):
        dot, color, label = STATE_CONFIG.get(state, ("●", "#555555", "Bereit"))
        self._dot.configure(text_color=color)
        self._label.configure(text=label)
        if state != State.TOOL_RUNNING:
            self._tool_label.configure(text="")

    def set_tool(self, tool_name: str):
        self._tool_label.configure(text=f"({tool_name})")

    def set_custom_text(self, text: str):
        self._label.configure(text=text)
