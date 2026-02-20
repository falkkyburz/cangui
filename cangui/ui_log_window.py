"""Application log panel that displays timestamped INFO/WARNING/ERROR messages.

LogWindow is a dockable panel hosted in a QTabWidget pane.  It accumulates
messages emitted by the rest of the application and presents them in a
scrolling list with colour-coded severity levels.
"""

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QWidget, QToolBar, QListWidget, QListWidgetItem

from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow


class LogWindow(BaseDockWindow):
    """Scrolling log panel that shows timestamped application messages.

    Messages are colour-coded by severity: ERROR entries are rendered in red,
    WARNING entries in orange, and INFO entries in the default palette colour.
    A toolbar *Clear* action removes all entries from the list.

    Signals:
        message_appended: Emitted after every call to :meth:`append`, allowing
            the main window to badge or highlight the Log tab.
    """

    TITLE = "Log"

    #: Emitted immediately after a new entry is added to the list.
    message_appended = Signal()

    def __init__(self, parent=None):
        """Create the log panel with a list widget and a clear toolbar action.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        clear_action = QAction(_icon("trash"), "Clear", self)
        clear_action.triggered.connect(self._list.clear)
        toolbar.addAction(clear_action)

        self._layout.addWidget(toolbar)
        self._layout.addWidget(self._list)

    def append(self, level: str, message: str):
        """Append a timestamped log entry to the list and scroll to it.

        The entry is prefixed with the current wall-clock time (HH:MM:SS).
        After the item is added, :attr:`message_appended` is emitted.

        Args:
            level: Severity string — one of ``'INFO'``, ``'WARNING'``, or
                ``'ERROR'``.  Any other value is displayed without special
                colouring.
            message: Human-readable description of the event.
        """
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
        """Return the log list widget for focus routing.

        Returns:
            The internal QListWidget that displays log entries.
        """
        return self._list
