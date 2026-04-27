import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.app_window import AppWindow


def main():
    app = AppWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
