from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer

from cangui.core.can_message import CanMessage
from cangui.core.signal_decoder import SignalDecoder


@dataclass
class WatchEntry:
    arb_id: int
    signal_name: str
    display_name: str = ""
    value: str = ""
    unit: str = ""
    direction: str = "Rx"

    @property
    def name(self) -> str:
        return self.display_name or self.signal_name


COLUMNS = ["Name", "Value", "Direction"]


class WatchModel(QAbstractTableModel):
    def __init__(self, decoder: SignalDecoder | None = None, parent=None):
        super().__init__(parent)
        self._entries: list[WatchEntry] = []
        self._decoder = decoder
        # Index for fast lookup: arb_id -> list of entry indices
        self._arb_id_to_entries: dict[int, list[int]] = {}
        self._pending: list[CanMessage] = []

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(100)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    def set_decoder(self, decoder: SignalDecoder):
        self._decoder = decoder

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
            case 0: return entry.name
            case 1:
                if entry.unit:
                    return f"{entry.value} {entry.unit}"
                return entry.value
            case 2: return entry.direction
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def add_watch(self, arb_id: int, signal_name: str, display_name: str = "",
                  unit: str = "", direction: str = "Rx"):
        # Check for duplicates
        for e in self._entries:
            if e.arb_id == arb_id and e.signal_name == signal_name:
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
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self._rebuild_index()
            self.endRemoveRows()

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
        self._arb_id_to_entries.clear()
        for i, entry in enumerate(self._entries):
            self._arb_id_to_entries.setdefault(entry.arb_id, []).append(i)

    @property
    def entries(self) -> list[WatchEntry]:
        return self._entries

    def clear(self):
        self.beginResetModel()
        self._entries.clear()
        self._arb_id_to_entries.clear()
        self._pending.clear()
        self.endResetModel()
