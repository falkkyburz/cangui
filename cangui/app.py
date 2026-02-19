import os
import sys

# QtAds drag-and-drop requires window positioning which native Wayland
# does not support.  Force XWayland on Wayland sessions.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["QT_QPA_PLATFORM"] = "xcb"

# Suppress "invalid style override" warnings — we force Fusion ourselves below.
os.environ.pop("QT_STYLE_OVERRIDE", None)

from PySide6.QtWidgets import QApplication

from cangui.ui_main_window import MainWindow
from cangui.theme import LIGHT_THEME


def _prepare_theme() -> str:
    """Write combo-box down-arrow SVGs to cache and inject their paths into the stylesheet."""
    from pathlib import Path

    cache = Path.home() / ".cache" / "cangui"
    cache.mkdir(parents=True, exist_ok=True)

    normal = cache / "down_arrow.svg"
    dimmed = cache / "down_arrow_dim.svg"
    normal.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 6">'
        '<path d="M0 0L5 6L10 0" fill="#5A5A5E"/></svg>'
    )
    dimmed.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 6">'
        '<path d="M0 0L5 6L10 0" fill="#AEAFB3"/></svg>'
    )

    def _url(p: Path) -> str:
        return str(p).replace("\\", "/")

    return (
        LIGHT_THEME
        .replace("__CANGUI_DOWN_ARROW_DIM__", _url(dimmed))
        .replace("__CANGUI_DOWN_ARROW__", _url(normal))
    )


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_prepare_theme())
    app.setApplicationName("cangui")
    app.setOrganizationName("cangui")

    window = MainWindow()
    window.show()

    # Open project file if passed as command line argument
    args = app.arguments()[1:]  # skip executable name
    if args and args[0].endswith(".json"):
        window.open_project(args[0])

    sys.exit(app.exec())
