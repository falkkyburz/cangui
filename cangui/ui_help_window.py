"""Keyboard shortcut reference panel displayed in the Help tab.

HelpWindow presents a read-only table of (Key, Action, Context) tuples so
users can quickly discover available keyboard shortcuts without leaving the
application.
"""

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QWidget, QTableView, QHeaderView

from cangui.ui_base_dock_window import BaseDockWindow


COLUMNS = ["Key", "Action", "Context"]


class HelpModel(QAbstractTableModel):
    """Read-only table model that backs the keyboard shortcut reference table.

    Each row is a ``(key, action, context)`` tuple.  The model is intended to
    be populated once at startup via :meth:`set_entries` and is never mutated
    afterwards.
    """

    def __init__(self, parent=None):
        """Initialize with an empty entry list.

        Args:
            parent: Optional parent QObject.
        """
        super().__init__(parent)
        self._entries: list[tuple[str, str, str]] = []

    def set_entries(self, entries: list[tuple[str, str, str]]):
        """Replace the entire shortcut list and notify attached views.

        Args:
            entries: Sequence of ``(key_label, action_description, context)``
                tuples.  A full model reset is issued so views repaint.
        """
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        """Return the number of shortcut entries.

        Args:
            parent: Must be an invalid index for a flat table model.

        Returns:
            Number of rows, or 0 when *parent* is valid.
        """
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed number of columns (3: Key, Action, Context).

        Args:
            parent: Unused for a flat table model.

        Returns:
            Always ``3``.
        """
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for the horizontal header.

        Args:
            section: Column index.
            orientation: Header orientation; only horizontal is handled.
            role: Data role; only DisplayRole returns a value.

        Returns:
            Column name string, or ``None`` for unhandled combinations.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return cell data for the given index and role.

        Args:
            index: Model index identifying the row and column.
            role: Data role; only DisplayRole returns a value.

        Returns:
            The cell string, or ``None`` for invalid indices or unsupported
            roles.
        """
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._entries[index.row()][index.column()]

    def flags(self, index: QModelIndex):
        """Return item flags — entries are enabled and selectable but not editable.

        Args:
            index: Model index (column is ignored).

        Returns:
            ``ItemIsEnabled | ItemIsSelectable``.
        """
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class HelpWindow(BaseDockWindow):
    """Keyboard shortcut reference panel displayed in the Help tab.

    Renders a read-only table of application keyboard shortcuts populated by
    the main window at startup via :meth:`set_entries`.
    """

    TITLE = "Help"

    def __init__(self, parent=None):
        """Create the help panel with a read-only shortcut table.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._model = HelpModel(self)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._layout.addWidget(self._table)

    @property
    def primary_view(self):
        """Return the shortcut table for focus routing.

        Returns:
            The internal QTableView that lists keyboard shortcuts.
        """
        return self._table

    def set_entries(self, entries: list[tuple[str, str, str]]):
        """Populate the shortcut table with the supplied entries.

        Delegates to :meth:`HelpModel.set_entries` which triggers a full model
        reset so attached views repaint.

        Args:
            entries: Sequence of ``(key_label, action_description, context)``
                tuples to display.
        """
        self._model.set_entries(entries)
