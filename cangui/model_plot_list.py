from dataclasses import dataclass

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder


@dataclass
class PlotEntry:
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
    def __init__(self, decoder: SignalDecoder | None = None, parent=None):
        super().__init__(parent)
        self._entries: list[PlotEntry] = []
        self._decoder = decoder
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
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = index.column()
        if col == COL_VISIBLE:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif col == COL_WIDTH:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
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
        if 0 <= row < len(self._entries):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._entries.pop(row)
            self._rebuild_index()
            self.endRemoveRows()

    def move_up(self, row: int):
        if row <= 0 or row >= len(self._entries):
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
        self._entries.insert(row - 1, self._entries.pop(row))
        self._rebuild_index()
        self.endMoveRows()

    def move_down(self, row: int):
        if row < 0 or row >= len(self._entries) - 1:
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
        self._entries.insert(row + 1, self._entries.pop(row))
        self._rebuild_index()
        self.endMoveRows()

    def on_message(self, msg: CanMessage):
        if msg.arbitration_id in self._arb_id_to_entries:
            self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        index = self._arb_id_to_entries
        for msg in messages:
            if msg.arbitration_id in index:
                self._pending.append(msg)

    def _flush(self):
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
        self._arb_id_to_entries.clear()
        for i, entry in enumerate(self._entries):
            self._arb_id_to_entries.setdefault(entry.arb_id, []).append(i)

    @property
    def entries(self) -> list[PlotEntry]:
        return self._entries

    def clear(self):
        self.beginResetModel()
        self._entries.clear()
        self._arb_id_to_entries.clear()
        self._pending.clear()
        self.endResetModel()

    def get_entry(self, arb_id: int, signal_name: str) -> "PlotEntry | None":
        for entry in self._entries:
            if entry.arb_id == arb_id and entry.signal_name == signal_name:
                return entry
        return None
