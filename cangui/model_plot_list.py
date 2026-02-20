"""Qt table model backing the Plot List panel.

Holds a list of :class:`PlotEntry` items, one per (arb_id, signal_name)
pair.  Incoming :class:`~cangui.can_message.CanMessage` objects are
buffered and flushed every 100 ms so the view is updated in batches rather
than on every raw CAN frame.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder


@dataclass
class PlotEntry:
    """Data record for a single plotted signal.

    Attributes:
        arb_id: CAN arbitration ID of the message that carries the signal.
        signal_name: Name of the decoded signal within the message.
        unit: Physical unit string displayed in the Unit column.
        color: Hex color string used for the plot line (default matplotlib blue).
        width: Line width in pixels (clamped 1–5).
        visible: Whether the series is currently shown in the plot.
        direction: Message direction label, either ``"Rx"`` or ``"Tx"``.
        value: Most-recently decoded display value as a string.
    """

    arb_id: int
    signal_name: str
    unit: str = ""
    color: str = "#1f77b4"
    width: int = 2
    visible: bool = True
    direction: str = "Rx"
    value: str = ""


COLUMNS = ["Name", "Value", "Color", "Width", "Visible", "Unit", "Direction", "Origin"]

COL_NAME = 0
COL_VALUE = 1
COL_COLOR = 2
COL_WIDTH = 3
COL_VISIBLE = 4
COL_UNIT = 5
COL_DIRECTION = 6
COL_ORIGIN = 7


class PlotListModel(QAbstractTableModel):
    """Table model that manages the list of signals shown in the Plot window.

    Each row corresponds to one :class:`PlotEntry`.  The model accepts
    incoming CAN messages via :meth:`on_message` / :meth:`on_messages`, buffers
    them, and flushes decoded values to the ``Value`` column on a 100 ms timer
    so the UI is not hammered by raw CAN traffic.
    """

    def __init__(self, decoder: SignalDecoder | None = None, parent=None):
        """Initialise the model with an optional signal decoder.

        Args:
            decoder: Optional :class:`~cangui.signal_decoder.SignalDecoder`
                used to turn raw CAN payloads into physical signal values.
                Can be set later via :meth:`set_decoder`.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._entries: list[PlotEntry] = []
        self._decoder = decoder
        self._arb_id_to_entries: dict[int, list[int]] = {}
        self._pending: list[CanMessage] = []

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(20)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    def set_decoder(self, decoder: SignalDecoder):
        """Replace the active signal decoder.

        Args:
            decoder: New :class:`~cangui.signal_decoder.SignalDecoder` instance
                to use for all subsequent message flushes.
        """
        self._decoder = decoder

    def rowCount(self, parent=QModelIndex()):
        """Return the number of plot entries (rows) in the model.

        Args:
            parent: Must be an invalid (root) index; child indices always
                return 0 because this is a flat table.

        Returns:
            Number of :class:`PlotEntry` rows, or 0 if *parent* is valid.
        """
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed number of columns defined by ``COLUMNS``.

        Args:
            parent: Unused; present for Qt override compatibility.

        Returns:
            Total number of columns (``len(COLUMNS)``).
        """
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header label for the given section.

        Args:
            section: Zero-based column index.
            orientation: Only horizontal headers are populated.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Column name string, or ``None`` for vertical headers or
            unhandled roles.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display, edit, or check-state data for the given cell.

        Args:
            index: Cell position in the model.
            role: Qt data role determining which aspect of the cell is returned.

        Returns:
            Cell value for ``DisplayRole``/``EditRole``/``CheckStateRole``,
            or ``None`` for unhandled roles or invalid indices.
        """
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole:
            if col == COL_VISIBLE:
                return Qt.CheckState.Checked if entry.visible else Qt.CheckState.Unchecked
            return None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match col:
                case 0: return entry.signal_name
                case 1: return entry.value
                case 2: return entry.color
                case 3: return entry.width
                case 4: return None  # checkbox only
                case 5: return entry.unit
                case 6: return entry.direction
                case 7:
                    msg_name = self._decoder.get_symbol(entry.arb_id) if self._decoder else ""
                    if msg_name:
                        return f"{msg_name}.{entry.signal_name}"
                    return entry.signal_name
        return None

    def flags(self, index: QModelIndex):
        """Return interaction flags for the given cell.

        The ``Visible`` column is checkable; the ``Width`` column is
        inline-editable; all other cells are read-only.

        Args:
            index: Cell position in the model.

        Returns:
            Combined :class:`Qt.ItemFlag` value for the cell.
        """
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = index.column()
        if col == COL_VISIBLE:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif col == COL_WIDTH:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        """Write a new value into the model and emit ``dataChanged``.

        Handles ``CheckStateRole`` for the ``Visible`` column and
        ``EditRole`` for the ``Color`` and ``Width`` columns.  Width values
        are clamped to the range [1, 5].

        Args:
            index: Cell position to update.
            value: New value to store.
            role: Qt data role; either ``EditRole`` or ``CheckStateRole``.

        Returns:
            ``True`` if the value was accepted and stored, ``False`` otherwise.
        """
        if not index.isValid():
            return False
        entry = self._entries[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == COL_VISIBLE:
            entry.visible = Qt.CheckState(value) == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            return True

        if role == Qt.ItemDataRole.EditRole:
            if col == COL_COLOR:
                entry.color = str(value)
                self.dataChanged.emit(index, index)
                return True
            if col == COL_WIDTH:
                try:
                    entry.width = max(1, min(5, int(value)))
                    self.dataChanged.emit(index, index)
                    return True
                except (ValueError, TypeError):
                    return False
        return False

    def add_entry(self, arb_id: int, signal_name: str, unit: str = "",
                  color: str = "#1f77b4", width: int = 2,
                  direction: str = "Rx") -> bool:
        """Add a new signal entry to the model if it is not already present.

        Duplicate entries (same *arb_id* and *signal_name*) are silently
        rejected.  On success the internal arb_id lookup index is rebuilt and
        Qt insert notifications are emitted.

        Args:
            arb_id: CAN arbitration ID of the source message.
            signal_name: Name of the decoded signal within that message.
            unit: Physical unit string for the ``Unit`` column.
            color: Hex color string for the plot line.
            width: Initial line width in pixels (clamped 1–5 on edit).
            direction: Message direction label, ``"Rx"`` or ``"Tx"``.

        Returns:
            ``True`` if the entry was added, ``False`` if it already existed.
        """
        for e in self._entries:
            if e.arb_id == arb_id and e.signal_name == signal_name:
                return False
        row = len(self._entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(PlotEntry(
            arb_id=arb_id, signal_name=signal_name, unit=unit,
            color=color, width=width, direction=direction,
        ))
        self._rebuild_index()
        self.endInsertRows()
        return True

    def remove_entry(self, row: int):
        """Remove the entry at the given row index.

        Does nothing if *row* is out of range.  Emits Qt remove notifications
        and rebuilds the arb_id lookup index.

        Args:
            row: Zero-based row index of the entry to remove.
        """
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self._rebuild_index()
            self.endRemoveRows()

    def move_up(self, row: int):
        """Move the entry at *row* one position up in the list.

        Does nothing if *row* is already at the top or out of range.

        Args:
            row: Zero-based row index of the entry to move.
        """
        if row <= 0 or row >= len(self._entries):
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
        self._entries.insert(row - 1, self._entries.pop(row))
        self._rebuild_index()
        self.endMoveRows()

    def move_down(self, row: int):
        """Move the entry at *row* one position down in the list.

        Does nothing if *row* is already at the bottom or out of range.

        Args:
            row: Zero-based row index of the entry to move.
        """
        if row < 0 or row >= len(self._entries) - 1:
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
        self._entries.insert(row + 1, self._entries.pop(row))
        self._rebuild_index()
        self.endMoveRows()

    def on_message(self, msg: CanMessage):
        """Queue a single incoming CAN message for the next flush cycle.

        Only messages whose arbitration ID matches at least one tracked entry
        are buffered; others are dropped immediately.

        Args:
            msg: Received CAN message to process.
        """
        if msg.arbitration_id in self._arb_id_to_entries:
            self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Queue a batch of incoming CAN messages for the next flush cycle.

        Only messages whose arbitration ID matches at least one tracked entry
        are buffered; others are dropped immediately.

        Args:
            messages: List of received CAN messages to process.
        """
        index = self._arb_id_to_entries
        for msg in messages:
            if msg.arbitration_id in index:
                self._pending.append(msg)

    def _flush(self):
        """Decode all pending messages and update the Value column in bulk.

        Called automatically by the 100 ms batch timer.  For each unique
        arbitration ID in the pending buffer only the most recent message is
        decoded.  A single ``dataChanged`` signal spanning the affected rows is
        emitted when any values change.
        """
        if not self._pending or self._decoder is None:
            return
        batch = self._pending
        self._pending = []

        latest: dict[int, CanMessage] = {}
        for msg in batch:
            latest[msg.arbitration_id] = msg

        changed_indices: set[int] = set()
        for arb_id, msg in latest.items():
            entries = self._arb_id_to_entries.get(arb_id)
            if not entries:
                continue
            decoded = self._decoder.decode(arb_id, msg.data)
            if not decoded:
                continue
            for idx in entries:
                entry = self._entries[idx]
                for ds in decoded:
                    if ds.name == entry.signal_name:
                        new_val = ds.display_value
                        if entry.value != new_val:
                            entry.value = new_val
                            changed_indices.add(idx)
                        break

        if changed_indices:
            min_idx = min(changed_indices)
            max_idx = max(changed_indices)
            self.dataChanged.emit(
                self.index(min_idx, COL_VALUE),
                self.index(max_idx, COL_VALUE),
            )

    def _rebuild_index(self):
        """Rebuild the arb_id-to-row-indices lookup dictionary.

        Called after any structural change (add, remove, reorder) so that
        :meth:`on_message` and :meth:`on_messages` can quickly decide whether
        an incoming message is relevant.
        """
        self._arb_id_to_entries.clear()
        for i, entry in enumerate(self._entries):
            self._arb_id_to_entries.setdefault(entry.arb_id, []).append(i)

    @property
    def entries(self) -> list[PlotEntry]:
        """Direct reference to the internal list of plot entries.

        Returns:
            Mutable list of :class:`PlotEntry` objects in display order.
        """
        return self._entries

    def clear(self):
        """Remove all plot entries and reset the model."""
        self.beginResetModel()
        self._entries.clear()
        self._arb_id_to_entries.clear()
        self._pending.clear()
        self.endResetModel()

    def get_entry(self, arb_id: int, signal_name: str) -> "PlotEntry | None":
        """Return the PlotEntry for a given signal, or ``None`` if not found.

        Args:
            arb_id: CAN arbitration ID of the message.
            signal_name: DBC signal name.

        Returns:
            The matching :class:`PlotEntry`, or ``None``.
        """
        for entry in self._entries:
            if entry.arb_id == arb_id and entry.signal_name == signal_name:
                return entry
        return None
