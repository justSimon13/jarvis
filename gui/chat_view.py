import customtkinter as ctk


class ChatView(ctk.CTkScrollableFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#111111", **kwargs)
        self._current_jarvis_bubble = None
        self._thinking_bubble = None

    def set_user_thinking(self, partial_text: str):
        if self._thinking_bubble is None:
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.pack(fill="x", pady=(6, 2), padx=12)
            self._thinking_bubble = ctk.CTkLabel(
                frame,
                text="",
                font=("SF Pro", 14, "italic"),
                text_color="#888888",
                fg_color="#1e1e1e",
                corner_radius=12,
                wraplength=520,
                justify="left",
                padx=12,
                pady=8,
                anchor="e",
            )
            self._thinking_bubble.pack(side="right")
        self._thinking_bubble.configure(text=f"{partial_text} 💭")
        self._scroll_to_bottom()

    def add_user_message(self, text: str):
        if self._thinking_bubble is not None:
            self._thinking_bubble.master.destroy()
            self._thinking_bubble = None
        self._current_jarvis_bubble = None
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", pady=(6, 2), padx=12)

        bubble = ctk.CTkLabel(
            frame,
            text=text,
            font=("SF Pro", 14),
            text_color="#e5e5e5",
            fg_color="#2a2a2a",
            corner_radius=12,
            wraplength=520,
            justify="left",
            padx=12,
            pady=8,
            anchor="e",
        )
        bubble.pack(side="right")

    def start_jarvis_message(self):
        self._current_jarvis_bubble = None
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", pady=(6, 2), padx=12)

        bubble = ctk.CTkLabel(
            frame,
            text="",
            font=("SF Pro", 14),
            text_color="#e0f2fe",
            fg_color="#0f3460",
            corner_radius=12,
            wraplength=520,
            justify="left",
            padx=12,
            pady=8,
            anchor="w",
        )
        bubble.pack(side="left")
        self._current_jarvis_bubble = bubble
        self._scroll_to_bottom()

    def append_to_jarvis_message(self, chunk: str):
        if self._current_jarvis_bubble is None:
            self.start_jarvis_message()
        current = self._current_jarvis_bubble.cget("text")
        self._current_jarvis_bubble.configure(text=current + chunk)
        self._scroll_to_bottom()

    def finalize_jarvis_message(self):
        self._current_jarvis_bubble = None

    def add_error(self, text: str):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", pady=(4, 2), padx=12)
        label = ctk.CTkLabel(
            frame,
            text=f"⚠ {text}",
            font=("SF Pro", 13),
            text_color="#ef4444",
            fg_color="transparent",
            anchor="w",
        )
        label.pack(side="left")

    def _scroll_to_bottom(self):
        self.after(10, lambda: self._parent_canvas.yview_moveto(1.0))
