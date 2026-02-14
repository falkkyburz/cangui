from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableView, QHeaderView


COLUMNS = ["Key", "Action", "Context"]


class HelpModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[tuple[str, str, str]] = []

    def set_entries(self, entries: list[tuple[str, str, str]]):
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._entries[index.row()][index.column()]

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class HelpWindow(QWidget):
    """Keyboard shortcut reference window."""

    TITLE = "Help"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = HelpModel(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self._table)

    @property
    def primary_view(self):
        return self._table

    def set_entries(self, entries: list[tuple[str, str, str]]):
        self._model.set_entries(entries)
