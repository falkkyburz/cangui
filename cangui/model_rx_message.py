from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QTimer
from PySide6.QtGui import QColor

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder
from cangui.model_rx_filter import RxFilterModel

# Internal ID encoding:
#   Top-level (message) rows: internalId = 0
#   Child (signal) rows:      internalId = parent_row + 1
_TOP_LEVEL = 0


@dataclass
class SignalItem:
    name: str = ""
    value: str = ""
    unit: str = ""


@dataclass
class RxMessageItem:
    bus: int = 0
    can_id: int = 0
    is_extended_id: bool = False
    is_error_frame: bool = False
    frame_type: str = ""
    length: int = 0
    symbol: str = ""
    raw_data: bytes = b""
    timing_errors: int = 0
    cycle_time_ms: float = 0.0
    count: int = 0
    last_timestamp: float = 0.0
    signals: list[SignalItem] = field(default_factory=list)


COLUMNS = ["Bus", "CAN-ID (hex)", "Type", "Length", "Symbol",
           "Data (hex)", "Timing Errors", "Cycle Time", "Count"]


class RxMessageModel(QAbstractItemModel):
    def __init__(self, decoder: SignalDecoder | None = None,
                 rx_filter: RxFilterModel | None = None, parent=None):
        super().__init__(parent)
        self._items: list[RxMessageItem] = []
        self._id_to_row: dict[tuple[int, int], int] = {}  # (bus, arb_id) -> row
        self._pending: list[CanMessage] = []
        self._decoder = decoder
        self._filter = rx_filter

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(50)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    def set_decoder(self, decoder: SignalDecoder):
        self._decoder = decoder

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            # Top-level message row
            return self.createIndex(row, column, _TOP_LEVEL)
        else:
            # Child signal row — encode parent's row
            return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index: QModelIndex):
        if not index.isValid():
            return QModelIndex()
        ptr = index.internalId()
        if ptr == _TOP_LEVEL:
            # Already a top-level row
            return QModelIndex()
        # Child row — return parent as a top-level index
        parent_row = ptr - 1
        return self.createIndex(parent_row, 0, _TOP_LEVEL)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._items)
        # Only top-level items have children
        if parent.internalId() == _TOP_LEVEL and 0 <= parent.row() < len(self._items):
            return len(self._items[parent.row()].signals)
        return 0

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def _is_top_level(self, index: QModelIndex) -> bool:
        return index.internalId() == _TOP_LEVEL

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if self._is_top_level(index) and 0 <= index.row() < len(self._items):
                item = self._items[index.row()]
                if item.is_error_frame:
                    return QColor(Qt.GlobalColor.red)
            return None

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        col = index.column()

        if self._is_top_level(index):
            if index.row() >= len(self._items):
                return None
            item = self._items[index.row()]
            match col:
                case 0: return item.bus
                case 1:
                    if item.is_extended_id:
                        return f"{item.can_id:08X}"
                    return f"{item.can_id:03X}"
                case 2: return item.frame_type
                case 3: return item.length
                case 4: return item.symbol
                case 5: return " ".join(f"{b:02X}" for b in item.raw_data[:item.length])
                case 6: return item.timing_errors if item.timing_errors else ""
                case 7: return f"{item.cycle_time_ms:.1f}" if item.cycle_time_ms else ""
                case 8: return item.count
        else:
            parent_row = index.internalId() - 1
            if parent_row >= len(self._items):
                return None
            sigs = self._items[parent_row].signals
            if index.row() >= len(sigs):
                return None
            sig = sigs[index.row()]
            match col:
                case 4: return sig.name
                case 5:
                    if sig.unit:
                        return f"{sig.value} {sig.unit}"
                    return sig.value
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_filter(self, rx_filter: RxFilterModel):
        self._filter = rx_filter

    def on_message(self, msg: CanMessage):
        if not msg.is_rx:
            return
        if self._filter and not self._filter.accepts(msg.arbitration_id, msg.bus):
            return
        self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        filt = self._filter
        for msg in messages:
            if not msg.is_rx:
                continue
            if filt and not filt.accepts(msg.arbitration_id, msg.bus):
                continue
            self._pending.append(msg)

    def _decode_signals(self, item: RxMessageItem):
        """Decode signals from raw data using the signal decoder."""
        if self._decoder is None:
            return
        decoded = self._decoder.decode(item.can_id, item.raw_data)
        if not decoded:
            return

        if not item.symbol:
            item.symbol = self._decoder.get_symbol(item.can_id)

        new_signals = []
        for ds in decoded:
            new_signals.append(SignalItem(
                name=ds.name,
                value=ds.display_value,
                unit=ds.unit,
            ))

        old_count = len(item.signals)
        new_count = len(new_signals)

        if old_count == new_count:
            for i, sig in enumerate(new_signals):
                item.signals[i].value = sig.value
        else:
            item.signals = new_signals

    def _flush(self):
        if not self._pending:
            return
        batch = self._pending
        self._pending = []

        rows_to_update: set[int] = set()

        for msg in batch:
            key = (msg.bus, msg.arbitration_id)
            row = self._id_to_row.get(key)
            if row is not None:
                item = self._items[row]
                now = msg.timestamp
                if item.last_timestamp > 0:
                    dt = (now - item.last_timestamp) * 1000
                    if item.cycle_time_ms > 0:
                        item.cycle_time_ms = item.cycle_time_ms * 0.8 + dt * 0.2
                    else:
                        item.cycle_time_ms = dt
                item.raw_data = msg.data
                item.length = msg.dlc
                item.count += 1
                item.last_timestamp = now
                rows_to_update.add(row)
            else:
                new_row = len(self._items)
                self.beginInsertRows(QModelIndex(), new_row, new_row)
                item = RxMessageItem(
                    bus=msg.bus,
                    can_id=msg.arbitration_id,
                    is_extended_id=msg.is_extended_id,
                    is_error_frame=msg.is_error_frame,
                    frame_type=msg.frame_type,
                    length=msg.dlc,
                    raw_data=msg.data,
                    count=1,
                    last_timestamp=msg.timestamp,
                )
                if self._decoder:
                    item.symbol = self._decoder.get_symbol(msg.arbitration_id)
                self._decode_signals(item)
                self._items.append(item)
                self._id_to_row[key] = new_row
                self.endInsertRows()

        # Decode signals once per updated row (not per message)
        for row in rows_to_update:
            self._decode_signals(self._items[row])

        if rows_to_update:
            min_row = min(rows_to_update)
            max_row = max(rows_to_update)
            self.dataChanged.emit(
                self.index(min_row, 0),
                self.index(max_row, self.columnCount() - 1),
            )
            # Also notify child signal rows
            for row in rows_to_update:
                item = self._items[row]
                if item.signals:
                    parent_idx = self.index(row, 0)
                    self.dataChanged.emit(
                        self.index(0, 0, parent_idx),
                        self.index(len(item.signals) - 1,
                                   self.columnCount() - 1, parent_idx),
                    )

    def clear(self):
        self.beginResetModel()
        self._items.clear()
        self._id_to_row.clear()
        self._pending.clear()
        self.endResetModel()

    def get_item(self, index: QModelIndex) -> RxMessageItem | None:
        if not index.isValid():
            return None
        if self._is_top_level(index):
            if 0 <= index.row() < len(self._items):
                return self._items[index.row()]
        else:
            parent_row = index.internalId() - 1
            if 0 <= parent_row < len(self._items):
                return self._items[parent_row]
        return None

    def get_signal_at(self, index: QModelIndex) -> tuple[RxMessageItem, SignalItem] | None:
        """Get the signal item at an index, if it's a signal child row."""
        if not index.isValid() or self._is_top_level(index):
            return None
        parent_row = index.internalId() - 1
        if 0 <= parent_row < len(self._items):
            item = self._items[parent_row]
            if 0 <= index.row() < len(item.signals):
                return item, item.signals[index.row()]
        return None

    @property
    def items(self) -> list[RxMessageItem]:
        return self._items

    def refresh_symbols(self):
        """Re-resolve symbol names and signals after a DBC is loaded."""
        if self._decoder is None:
            return
        for item in self._items:
            sym = self._decoder.get_symbol(item.can_id)
            if sym:
                item.symbol = sym
            self._decode_signals(item)
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, self.columnCount() - 1),
            )
