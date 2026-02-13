from PySide6.QtWidgets import QWidget, QVBoxLayout


class BaseDockWindow(QWidget):
    """Base class for all dockable windows."""

    TITLE = "Untitled"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    @property
    def primary_view(self):
        return None
