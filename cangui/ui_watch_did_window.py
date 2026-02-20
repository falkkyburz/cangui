"""UDS DID watch panel — periodically polls a list of DIDs and shows decoded values."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolBar,
    QTableView, QHeaderView, QLineEdit, QPushButton,
    QLabel, QSpinBox,
)
from PySide6.QtGui import QAction

from cangui.uds_client import UdsResponse
from cangui.service_uds import UdsService
from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow


@dataclass
class DidWatchEntry:
    """Data container for a single DID watch row.

    Attributes:
        did: UDS Data Identifier number.
        name: Human-readable label shown in the Name column.
        value: Last decoded string value of the DID response.
        raw_data: Raw byte payload of the last successful response.
        cycle_ms: Polling interval in milliseconds.
        error: Error description from the last failed response, or empty string.
    """

    did: int
    name: str
    value: str = ""
    raw_data: bytes = b""
    cycle_ms: int = 500
    error: str = ""


COLUMNS = ["DID", "Name", "Value", "Raw", "Cycle (ms)", "Status"]


class DidWatchModel(QAbstractTableModel):
    """Table model backing the DID watch view, holding a list of DidWatchEntry rows."""

    def __init__(self, parent=None):
        """Initialise with an empty entry list.

        Args:
            parent: Optional parent QObject.
        """
        super().__init__(parent)
        self._entries: list[DidWatchEntry] = []

    def rowCount(self, parent=QModelIndex()):
        """Return the number of DID watch entries.

        Args:
            parent: Unused; returns 0 for any valid parent (flat table).
        """
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed number of columns defined by COLUMNS.

        Args:
            parent: Unused parent index.
        """
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return horizontal header labels from the COLUMNS list.

        Args:
            section: Column index.
            orientation: Header orientation (Horizontal or Vertical).
            role: Data role; only DisplayRole is handled.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return cell display text for a given index.

        Args:
            index: Model index identifying the row and column.
            role: Data role; only DisplayRole returns a value.
        """
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
        """Return item flags; all cells are enabled and selectable but not editable.

        Args:
            index: Model index to query.
        """
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def add_entry(self, did: int, name: str = "", cycle_ms: int = 500):
        """Append a new DID entry if it does not already exist in the table.

        Args:
            did: UDS Data Identifier to add.
            name: Optional label; defaults to "DID 0x<did>" if empty.
            cycle_ms: Polling interval in milliseconds.
        """
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
        """Remove the entry at the given row index.

        Args:
            row: Zero-based row index to remove.
        """
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self.endRemoveRows()

    def move_up(self, row: int):
        """Move the entry at row one position upward in the list.

        Args:
            row: Zero-based row index of the entry to move up.
        """
        if row <= 0 or row >= len(self._entries):
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
        self._entries.insert(row - 1, self._entries.pop(row))
        self.endMoveRows()

    def move_down(self, row: int):
        """Move the entry at row one position downward in the list.

        Args:
            row: Zero-based row index of the entry to move down.
        """
        if row < 0 or row >= len(self._entries) - 1:
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
        self._entries.insert(row + 1, self._entries.pop(row))
        self.endMoveRows()

    def update_value(self, did: int, data: bytes):
        """Store a successful DID response and emit dataChanged for the affected row.

        Args:
            did: DID identifier whose row should be updated.
            data: Raw byte payload returned by the ECU.
        """
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
        """Record an error for a DID and emit dataChanged for the Status column.

        Args:
            did: DID identifier whose row should show the error.
            error: Human-readable error description.
        """
        for i, entry in enumerate(self._entries):
            if entry.did == did:
                entry.error = error
                self.dataChanged.emit(
                    self.index(i, 5), self.index(i, 5)
                )
                return

    @property
    def entries(self) -> list[DidWatchEntry]:
        """Return the list of all current DidWatchEntry objects."""
        return self._entries

    def clear(self):
        """Remove all entries and reset the model."""
        self.beginResetModel()
        self._entries.clear()
        self.endResetModel()


class WatchDidWindow(BaseDockWindow):
    """Periodic DID polling window."""

    TITLE = "Watch DID"

    add_to_plot_requested = Signal(int, str, str)  # arb_id (DID), signal_name, unit
    """Emitted when the user requests a DID value be added to the plot (arb_id, signal_name, unit)."""

    def __init__(self, uds_service: UdsService, parent=None):
        """Initialise the watch panel, build the toolbar, add-DID row and table view.

        Args:
            uds_service: UDS service used to issue ReadDID requests.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._uds = uds_service
        self._polling = False
        self._poll_index = 0

        self._model = DidWatchModel(self)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._start_action = QAction(_icon("play"), "Start Polling", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._stop_action = QAction(_icon("stop"), "Stop Polling", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        remove_action = QAction(_icon("remove"), "Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction(_icon("trash"), "Clear All", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Move Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Move Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        toolbar.addSeparator()

        add_to_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        add_to_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(add_to_plot_action)

        self._layout.addWidget(toolbar)

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
        self._layout.addLayout(add_layout)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._layout.addWidget(self._table)

        self._model.rowsInserted.connect(lambda *_: self._resize_columns())
        self._model.modelReset.connect(lambda *_: self._resize_columns())
        QTimer.singleShot(0, self._resize_columns)

        # Poll timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_next)

        # Wire UDS responses
        self._uds.response_received.connect(self._on_response)

    def _resize_columns(self):
        """Resize all columns except the last to fit their content."""
        for i in range(len(COLUMNS) - 1):
            self._table.resizeColumnToContents(i)

    @property
    def primary_view(self):
        """Return the main QTableView for focus and keyboard navigation."""
        return self._table

    def _on_add(self):
        """Read the add-row controls and insert a new DID entry into the model."""
        try:
            did = int(self._did_edit.text(), 16)
        except ValueError:
            return
        name = self._name_edit.text().strip()
        cycle = self._cycle_spin.value()
        self._model.add_entry(did, name, cycle)

    def _on_remove(self):
        """Remove all selected DID entries from the model."""
        rows = sorted(
            {i.row() for i in self._table.selectionModel().selectedIndexes()
             if i.column() == 0},
            reverse=True,
        )
        if not rows:
            idx = self._table.currentIndex()
            if idx.isValid():
                rows = [idx.row()]
        for row in rows:
            self._model.remove_entry(row)

    def _on_move_up(self):
        """Move the selected DID entry one row upward and keep it selected."""
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(row)
        self._table.setCurrentIndex(self._model.index(row - 1, 0))

    def _on_move_down(self):
        """Move the selected DID entry one row downward and keep it selected."""
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(row)
        self._table.setCurrentIndex(self._model.index(row + 1, 0))

    def _on_clear(self):
        """Stop polling and remove all DID entries from the model."""
        self._on_stop()
        self._model.clear()

    def _on_start(self):
        """Begin round-robin DID polling if there is at least one entry."""
        if not self._model.entries:
            return
        self._polling = True
        self._poll_index = 0
        self._start_action.setEnabled(False)
        self._stop_action.setEnabled(True)
        self._poll_next()

    def _on_stop(self):
        """Halt the poll timer and re-enable the Start action."""
        self._polling = False
        self._poll_timer.stop()
        self._start_action.setEnabled(True)
        self._stop_action.setEnabled(False)

    def _poll_next(self):
        """Issue a ReadDID request for the current poll index and schedule the next tick."""
        if not self._polling or not self._model.entries:
            return
        entry = self._model.entries[self._poll_index]
        self._uds.read_did(entry.did)
        # Schedule next poll using this entry's cycle time
        self._poll_index = (self._poll_index + 1) % len(self._model.entries)
        next_entry = self._model.entries[self._poll_index]
        self._poll_timer.start(next_entry.cycle_ms)

    def _on_add_to_plot(self):
        """Emit add_to_plot_requested for every selected DID entry."""
        rows = sorted(
            {i.row() for i in self._table.selectionModel().selectedIndexes()
             if i.column() == 0}
        )
        if not rows:
            idx = self._table.currentIndex()
            if idx.isValid():
                rows = [idx.row()]
        for row in rows:
            if row < len(self._model.entries):
                entry = self._model.entries[row]
                self.add_to_plot_requested.emit(entry.did, entry.name, "")

    def _on_response(self, resp: UdsResponse):
        """Handle an incoming UDS response and update the matching DID row.

        Args:
            resp: UDS response object; only ReadDID responses are processed.
        """
        if resp.service_name != "ReadDID" or resp.did == 0:
            return
        if resp.success:
            self._model.update_value(resp.did, resp.data)
        else:
            self._model.update_error(resp.did, resp.error)
