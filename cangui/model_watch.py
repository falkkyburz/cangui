"""Qt table model for the Watch panel.

Tracks a user-selected set of ``(arb_id, signal_name)`` pairs and keeps their
latest decoded values updated.  Incoming :class:`~cangui.can_message.CanMessage`
objects are buffered and decoded on a 100 ms timer so the UI is not hammered
by high-frequency CAN traffic.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder


@dataclass
class WatchEntry:
    """One row in the Watch table, representing a single monitored signal.

    Attributes:
        arb_id: CAN arbitration ID of the source message.
        signal_name: DBC signal name within that message.
        display_name: Optional human-readable label; falls back to *signal_name*.
        value: Latest decoded value as a string (updated on each flush).
        unit: Physical unit string appended to the displayed value.
        direction: Message direction label, either ``"Rx"`` or ``"Tx"``.
    """

    arb_id: int
    signal_name: str
    display_name: str = ""
    value: str = ""
    unit: str = ""
    direction: str = "Rx"

    @property
    def name(self) -> str:
        """Return display_name if set, otherwise signal_name."""
        return self.display_name or self.signal_name


COLUMNS = ["Name", "Value", "Direction", "Origin"]


class WatchModel(QAbstractTableModel):
    """Table model that displays the latest decoded value for each watched signal.

    Columns: Name, Value (with unit), Direction, Origin (message.signal notation).
    Incoming CAN messages are accumulated in ``_pending`` and decoded in batch
    every 100 ms so the UI is not refreshed on every raw frame.
    """

    def __init__(self, decoder: SignalDecoder | None = None, parent=None):
        """Initialise the WatchModel with an optional signal decoder.

        Args:
            decoder: Optional :class:`~cangui.signal_decoder.SignalDecoder` used
                to convert raw payloads into physical values.  Can be set later
                via :meth:`set_decoder`.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._entries: list[WatchEntry] = []
        self._decoder = decoder
        # Index for fast lookup: arb_id -> list of entry indices
        self._arb_id_to_entries: dict[int, list[int]] = {}
        self._pending: list[CanMessage] = []

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(20)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    def set_decoder(self, decoder: SignalDecoder):
        """Replace the active signal decoder.

        Args:
            decoder: New :class:`~cangui.signal_decoder.SignalDecoder` to use
                for all subsequent flush cycles.
        """
        self._decoder = decoder

    def rowCount(self, parent=QModelIndex()):
        """Return the number of watched signal rows.

        Args:
            parent: Must be an invalid (root) index; returns 0 for valid parents.

        Returns:
            Number of :class:`WatchEntry` rows, or 0 if *parent* is valid.
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
        """Return column header labels for the horizontal header.

        Args:
            section: Zero-based column index.
            orientation: Only horizontal headers are populated.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Column name string, or ``None`` for vertical headers or unhandled roles.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display text for the given cell.

        The Value column (1) appends the unit string when present.  The Origin
        column (3) formats as ``MessageName.SignalName`` when a decoder is
        available.

        Args:
            index: Cell position in the model.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Cell text, or ``None`` for unhandled roles or invalid indices.
        """
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        entry = self._entries[index.row()]
        match index.column():
            case 0: return entry.name
            case 1:
                if entry.unit:
                    return f"{entry.value} {entry.unit}"
                return entry.value
            case 2: return entry.direction
            case 3:
                msg_name = self._decoder.get_symbol(entry.arb_id) if self._decoder else ""
                if msg_name:
                    return f"{msg_name}.{entry.signal_name}"
                return entry.signal_name
        return None

    def flags(self, index: QModelIndex):
        """Return read-only item flags (enabled and selectable, not editable).

        Args:
            index: Cell position (unused beyond validity check).

        Returns:
            Combined :class:`Qt.ItemFlag` value.
        """
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def add_watch(self, arb_id: int, signal_name: str, display_name: str = "",
                  unit: str = "", direction: str = "Rx"):
        """Add a signal to the watch list if not already present.

        Duplicate entries (same *arb_id*, *signal_name*, and *direction*) are
        silently rejected.

        Args:
            arb_id: CAN arbitration ID of the source message.
            signal_name: DBC signal name within that message.
            display_name: Optional label shown instead of *signal_name*.
            unit: Physical unit string displayed alongside the value.
            direction: ``"Rx"`` or ``"Tx"`` direction label.
        """
        # Check for duplicates
        for e in self._entries:
            if e.arb_id == arb_id and e.signal_name == signal_name and e.direction == direction:
                return
        row = len(self._entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(WatchEntry(
            arb_id=arb_id,
            signal_name=signal_name,
            display_name=display_name,
            unit=unit,
            direction=direction,
        ))
        self._rebuild_index()
        self.endInsertRows()

    def remove_watch(self, row: int):
        """Remove the entry at the given row index.

        Does nothing if *row* is out of range.

        Args:
            row: Zero-based row index of the entry to remove.
        """
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self._rebuild_index()
            self.endRemoveRows()

    def move_up(self, row: int):
        """Move the entry at *row* one position up.

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
        """Move the entry at *row* one position down.

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
        """Queue a single message for processing."""
        if msg.arbitration_id in self._arb_id_to_entries:
            self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Queue a batch of messages for processing."""
        index = self._arb_id_to_entries
        for msg in messages:
            if msg.arbitration_id in index:
                self._pending.append(msg)

    def _flush(self):
        """Process pending messages and update watched signal values."""
        if not self._pending or self._decoder is None:
            return
        batch = self._pending
        self._pending = []

        # Keep only the last message per arb_id (latest value wins)
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
                            if not entry.unit and ds.unit:
                                entry.unit = ds.unit
                            changed_indices.add(idx)
                        break

        if changed_indices:
            min_idx = min(changed_indices)
            max_idx = max(changed_indices)
            self.dataChanged.emit(
                self.index(min_idx, 1),
                self.index(max_idx, 1),
            )

    def _rebuild_index(self):
        """Rebuild the arb_id-to-row-indices lookup after any structural change."""
        self._arb_id_to_entries.clear()
        for i, entry in enumerate(self._entries):
            self._arb_id_to_entries.setdefault(entry.arb_id, []).append(i)

    @property
    def entries(self) -> list[WatchEntry]:
        """Direct reference to the internal list of watch entries.

        Returns:
            Mutable list of :class:`WatchEntry` objects in display order.
        """
        return self._entries

    def clear(self):
        """Remove all watch entries and reset the model."""
        self.beginResetModel()
        self._entries.clear()
        self._arb_id_to_entries.clear()
        self._pending.clear()
        self.endResetModel()
