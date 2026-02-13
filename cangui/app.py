import os
import sys

# QtAds drag-and-drop requires window positioning which native Wayland
# does not support.  Force XWayland on Wayland sessions.
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PySide6.QtWidgets import QApplication

from cangui.ui.main_window import MainWindow


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("cangui")
    app.setOrganizationName("cangui")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
