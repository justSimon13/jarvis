import threading
import sys
from PIL import Image, ImageDraw


def _make_icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#0f3460")
    draw.text((18, 16), "J", fill="#e0f2fe", font=None)
    return img


class TrayIcon:
    def __init__(self, on_show_window, on_toggle_mode, on_quit, get_mode):
        self._on_show = on_show_window
        self._on_toggle_mode = on_toggle_mode
        self._on_quit = on_quit
        self._get_mode = get_mode
        self._icon = None
        self._thread = None

    def start(self):
        if sys.platform != "darwin":
            return
        try:
            import pystray
        except ImportError:
            return

        def build_menu():
            mode = self._get_mode()
            mode_label = "→ Text Mode" if mode == "voice" else "→ Voice Mode"
            return pystray.Menu(
                pystray.MenuItem("J.A.R.V.I.S.", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Fenster anzeigen", lambda: self._on_show()),
                pystray.MenuItem(mode_label, lambda: self._on_toggle_mode()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Beenden", lambda: self._on_quit()),
            )

        self._icon = pystray.Icon(
            "JARVIS",
            icon=_make_icon(),
            title="J.A.R.V.I.S.",
            menu=build_menu(),
        )

        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="TrayIcon")
        self._thread.start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
