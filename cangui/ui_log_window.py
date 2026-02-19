from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolBar, QListWidget, QListWidgetItem

from cangui.icons import icon as _icon


class LogWindow(QWidget):
    TITLE = "Log"

    message_appended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        clear_action = QAction(_icon("trash"), "Clear", self)
        clear_action.triggered.connect(self._list.clear)
        toolbar.addAction(clear_action)

        layout.addWidget(toolbar)
        layout.addWidget(self._list)

    def append(self, level: str, message: str):
        """Append a log entry. level is 'INFO', 'WARNING', or 'ERROR'."""
        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{ts}]  {level}  {message}")
        if level == "ERROR":
            item.setForeground(QColor("#C62828"))
        elif level == "WARNING":
            item.setForeground(QColor("#E65100"))
        self._list.addItem(item)
        self._list.scrollToBottom()
        self.message_appended.emit()

    @property
    def primary_view(self):
        return self._list
