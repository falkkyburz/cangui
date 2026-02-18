import os
import sys

# QtAds drag-and-drop requires window positioning which native Wayland
# does not support.  Force XWayland on Wayland sessions.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PySide6.QtWidgets import QApplication

from cangui.ui_main_window import MainWindow
from cangui.theme import LIGHT_THEME


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_THEME)
    app.setApplicationName("cangui")
    app.setOrganizationName("cangui")

    window = MainWindow()
    window.show()

    # Open project file if passed as command line argument
    args = app.arguments()[1:]  # skip executable name
    if args and args[0].endswith(".json"):
        window.open_project(args[0])

    sys.exit(app.exec())
