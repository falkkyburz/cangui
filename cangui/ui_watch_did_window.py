from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolBar,
    QTableView, QHeaderView, QLineEdit, QPushButton,
    QLabel, QSpinBox,
)
from PySide6.QtGui import QAction

from cangui.uds_client import UdsResponse
from cangui.service_uds import UdsService


@dataclass
class DidWatchEntry:
    did: int
    name: str
    value: str = ""
    raw_data: bytes = b""
    cycle_ms: int = 500
    error: str = ""


COLUMNS = ["DID", "Name", "Value", "Raw", "Cycle (ms)", "Status"]


class DidWatchModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[DidWatchEntry] = []

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
        entry = self._entries[index.row()]
        match index.column():
            case 0: return f"0x{entry.did:04X}"
            case 1: return entry.name
            case 2: return entry.value
            case 3: return " ".join(f"{b:02X}" for b in entry.raw_data)
            case 4: return entry.cycle_ms
            case 5: return entry.error or "OK" if entry.raw_data else ""
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def add_entry(self, did: int, name: str = "", cycle_ms: int = 500):
        for e in self._entries:
            if e.did == did:
                return
        row = len(self._entries)
        if not name:
            name = f"DID 0x{did:04X}"
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(DidWatchEntry(did=did, name=name, cycle_ms=cycle_ms))
        self.endInsertRows()

    def remove_entry(self, row: int):
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self.endRemoveRows()

    def update_value(self, did: int, data: bytes):
        for i, entry in enumerate(self._entries):
            if entry.did == did:
                entry.raw_data = data
                # Try ASCII interpretation
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                entry.value = printable
                entry.error = ""
                self.dataChanged.emit(
                    self.index(i, 2), self.index(i, 5)
                )
                return

    def update_error(self, did: int, error: str):
        for i, entry in enumerate(self._entries):
            if entry.did == did:
                entry.error = error
                self.dataChanged.emit(
                    self.index(i, 5), self.index(i, 5)
                )
                return

    @property
    def entries(self) -> list[DidWatchEntry]:
        return self._entries

    def clear(self):
        self.beginResetModel()
        self._entries.clear()
        self.endResetModel()


class WatchDidWindow(QWidget):
    """Periodic DID polling window."""

    TITLE = "Watch DID"

    add_to_plot_requested = Signal(int, str, str)  # arb_id (DID), signal_name, unit

    def __init__(self, uds_service: UdsService, parent=None):
        super().__init__(parent)
        self._uds = uds_service
        self._polling = False
        self._poll_index = 0

        self._model = DidWatchModel(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._start_action = QAction("Start Polling", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._stop_action = QAction("Stop Polling", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        remove_action = QAction("Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction("Clear All", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        add_to_plot_action = QAction("Add to Plot", self)
        add_to_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(add_to_plot_action)

        layout.addWidget(toolbar)

        # Add DID row
        add_layout = QHBoxLayout()
        add_layout.setContentsMargins(4, 2, 4, 2)
        add_layout.addWidget(QLabel("DID (hex):"))
        self._did_edit = QLineEdit("F190")
        self._did_edit.setMaximumWidth(80)
        add_layout.addWidget(self._did_edit)

        add_layout.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. VIN")
        self._name_edit.setMaximumWidth(120)
        add_layout.addWidget(self._name_edit)

        add_layout.addWidget(QLabel("Cycle:"))
        self._cycle_spin = QSpinBox()
        self._cycle_spin.setRange(50, 60000)
        self._cycle_spin.setValue(500)
        self._cycle_spin.setSuffix(" ms")
        self._cycle_spin.setMaximumWidth(100)
        add_layout.addWidget(self._cycle_spin)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add)
        add_layout.addWidget(add_btn)

        add_layout.addStretch()
        layout.addLayout(add_layout)

        # Table
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

        # Poll timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_next)

        # Wire UDS responses
        self._uds.response_received.connect(self._on_response)

    @property
    def primary_view(self):
        return self._table

    def _on_add(self):
        try:
            did = int(self._did_edit.text(), 16)
        except ValueError:
            return
        name = self._name_edit.text().strip()
        cycle = self._cycle_spin.value()
        self._model.add_entry(did, name, cycle)

    def _on_remove(self):
        index = self._table.currentIndex()
        if index.isValid():
            self._model.remove_entry(index.row())

    def _on_clear(self):
        self._on_stop()
        self._model.clear()

    def _on_start(self):
        if not self._model.entries:
            return
        self._polling = True
        self._poll_index = 0
        self._start_action.setEnabled(False)
        self._stop_action.setEnabled(True)
        self._poll_next()

    def _on_stop(self):
        self._polling = False
        self._poll_timer.stop()
        self._start_action.setEnabled(True)
        self._stop_action.setEnabled(False)

    def _poll_next(self):
        if not self._polling or not self._model.entries:
            return
        entry = self._model.entries[self._poll_index]
        self._uds.read_did(entry.did)
        # Schedule next poll using this entry's cycle time
        self._poll_index = (self._poll_index + 1) % len(self._model.entries)
        next_entry = self._model.entries[self._poll_index]
        self._poll_timer.start(next_entry.cycle_ms)

    def _on_add_to_plot(self):
        index = self._table.currentIndex()
        if not index.isValid():
            return
        entry = self._model.entries[index.row()]
        self.add_to_plot_requested.emit(entry.did, entry.name, "")

    def _on_response(self, resp: UdsResponse):
        if resp.service_name != "ReadDID" or resp.did == 0:
            return
        if resp.success:
            self._model.update_value(resp.did, resp.data)
        else:
            self._model.update_error(resp.did, resp.error)
