from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex

from cangui.core.signal_decoder import SignalDecoder

# Internal ID encoding (same scheme as RxMessageModel):
#   Top-level (message) rows: internalId = 0
#   Child (signal) rows:      internalId = parent_row + 1
_TOP_LEVEL = 0


@dataclass
class TxSignalItem:
    name: str = ""
    value: object = 0  # float, int, or str
    unit: str = ""


@dataclass
class TxMessageItem:
    bus: int = 1
    can_id: int = 0
    is_extended_id: bool = False
    frame_type: str = "Data"
    length: int = 8
    symbol: str = ""
    raw_data: bytearray = field(default_factory=lambda: bytearray(8))
    cycle_time_ms: int = 100
    cycle_enabled: bool = False
    count: int = 0
    trigger: str = "Time"
    creator: str = "User"
    signals: list[TxSignalItem] = field(default_factory=list)


COLUMNS = ["Bus", "CAN-ID (hex)", "Type", "Length", "Symbol",
           "Data (hex)", "Cycle Time", "Count", "Trigger", "Creator"]


class TxMessageModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[TxMessageItem] = []
        self._decoder: SignalDecoder | None = None

    def set_decoder(self, decoder: SignalDecoder):
        self._decoder = decoder

    # -- QAbstractItemModel required overrides --

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, _TOP_LEVEL)
        else:
            return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index: QModelIndex):
        if not index.isValid():
            return QModelIndex()
        ptr = index.internalId()
        if ptr == _TOP_LEVEL:
            return QModelIndex()
        parent_row = ptr - 1
        return self.createIndex(parent_row, 0, _TOP_LEVEL)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._items)
        if parent.internalId() == _TOP_LEVEL and 0 <= parent.row() < len(self._items):
            return len(self._items[parent.row()].signals)
        return 0

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def _is_top_level(self, index: QModelIndex) -> bool:
        return index.internalId() == _TOP_LEVEL

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col = index.column()

        if self._is_top_level(index):
            if index.row() >= len(self._items):
                return None
            item = self._items[index.row()]

            if role == Qt.ItemDataRole.CheckStateRole and col == 6:
                return Qt.CheckState.Checked if item.cycle_enabled else Qt.CheckState.Unchecked

            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
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
                    case 6: return item.cycle_time_ms
                    case 7: return item.count
                    case 8: return "Time" if item.cycle_enabled else "Wait"
                    case 9: return item.creator
        else:
            # Signal child row
            parent_row = index.internalId() - 1
            if parent_row >= len(self._items):
                return None
            sigs = self._items[parent_row].signals
            if index.row() >= len(sigs):
                return None
            sig = sigs[index.row()]

            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                match col:
                    case 4: return sig.name
                    case 5:
                        if sig.unit:
                            return f"{sig.value} {sig.unit}"
                        return str(sig.value)

        return None

    def flags(self, index: QModelIndex):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return flags

        if self._is_top_level(index):
            if index.column() == 6:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            if index.column() in (0, 1, 3, 4, 5, 6):
                flags |= Qt.ItemFlag.ItemIsEditable
        else:
            # Signal child rows: data column (5) is editable
            if index.column() == 5:
                flags |= Qt.ItemFlag.ItemIsEditable

        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False

        if self._is_top_level(index):
            return self._set_top_level_data(index, value, role)
        else:
            return self._set_signal_data(index, value, role)

    def _set_top_level_data(self, index, value, role):
        if index.row() >= len(self._items):
            return False
        item = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 6:
            item.cycle_enabled = Qt.CheckState(value) == Qt.CheckState.Checked
            # Notify both the checkbox column and the Trigger column
            trigger_idx = self.index(index.row(), 8)
            self.dataChanged.emit(index, trigger_idx)
            return True

        if role == Qt.ItemDataRole.EditRole:
            match col:
                case 0:
                    try:
                        item.bus = int(value)
                    except (ValueError, TypeError):
                        return False
                case 1:
                    try:
                        item.can_id = int(str(value), 16)
                    except ValueError:
                        return False
                    self._resolve_from_db(item)
                    self._rebuild_signals(index.row())
                case 3:
                    try:
                        length = int(value)
                        if 0 <= length <= 64:
                            item.length = length
                            if len(item.raw_data) < length:
                                item.raw_data.extend(b'\x00' * (length - len(item.raw_data)))
                            elif len(item.raw_data) > length:
                                item.raw_data = item.raw_data[:length]
                    except (ValueError, TypeError):
                        return False
                case 4:
                    name = str(value).strip()
                    if not name or self._decoder is None:
                        return False
                    arb_id = self._decoder.get_id_by_symbol(name)
                    if arb_id is None:
                        return False
                    item.can_id = arb_id
                    self._resolve_from_db(item)
                    self._rebuild_signals(index.row())
                case 5:
                    try:
                        data = bytes.fromhex(str(value).replace(" ", ""))
                        item.raw_data = bytearray(data)
                        item.length = len(data)
                    except ValueError:
                        return False
                    self._redecode_signals(index.row())
                case 6:
                    try:
                        item.cycle_time_ms = int(value)
                    except (ValueError, TypeError):
                        return False
                case _:
                    return False
            # CAN-ID / Symbol change updates the whole row (symbol, length, data, cycle)
            if col in (1, 4):
                left = self.index(index.row(), 0)
                right = self.index(index.row(), self.columnCount() - 1)
                self.dataChanged.emit(left, right)
            else:
                self.dataChanged.emit(index, index)
            return True
        return False

    def _set_signal_data(self, index, value, role):
        if role != Qt.ItemDataRole.EditRole or index.column() != 5:
            return False

        parent_row = index.internalId() - 1
        if parent_row >= len(self._items):
            return False
        item = self._items[parent_row]
        sig_idx = index.row()
        if sig_idx >= len(item.signals):
            return False

        sig = item.signals[sig_idx]
        # Parse the value — strip unit if present
        val_str = str(value).strip()
        if sig.unit and val_str.endswith(sig.unit):
            val_str = val_str[:-len(sig.unit)].strip()
        try:
            parsed = float(val_str)
            if parsed == int(parsed):
                parsed = int(parsed)
        except ValueError:
            parsed = val_str  # Keep as string for enum choices

        sig.value = parsed
        self.dataChanged.emit(index, index)

        # Re-encode all signals back into raw_data
        self._encode_signals(parent_row)
        return True

    # -- Signal / DBC helpers --

    def _resolve_from_db(self, item: TxMessageItem, override: bool = True):
        """Apply database info to item.

        If override is True, DLC/cycle_time/raw_data are overwritten from the
        database (used for new messages and CAN-ID changes).  If False, only
        the symbol name is updated (used for DBC reload on existing messages).
        """
        if self._decoder is None:
            return
        sym = self._decoder.get_symbol(item.can_id)
        if sym:
            item.symbol = sym
        if not override:
            return
        info = self._decoder.get_message_info(item.can_id)
        if info is not None:
            length, cycle_time = info
            item.length = length
            if cycle_time is not None:
                item.cycle_time_ms = int(cycle_time)
            # Encode initial signal values into raw_data
            sigs = self._decoder.get_signals_for_id(item.can_id)
            signal_data = {s.name: s.value for s in sigs}
            encoded = self._decoder.encode(item.can_id, signal_data)
            item.raw_data = bytearray(encoded) if encoded else bytearray(length)

    def _rebuild_signals(self, row: int):
        """Rebuild signal children from the decoder for a given message row."""
        item = self._items[row]
        parent_idx = self.index(row, 0)
        old_count = len(item.signals)

        if self._decoder is None:
            if old_count > 0:
                self.beginRemoveRows(parent_idx, 0, old_count - 1)
                item.signals.clear()
                self.endRemoveRows()
            return

        new_sigs = self._decoder.get_signals_for_id(item.can_id)
        new_count = len(new_sigs)

        if old_count > 0:
            self.beginRemoveRows(parent_idx, 0, old_count - 1)
            item.signals.clear()
            self.endRemoveRows()

        if new_count > 0:
            self.beginInsertRows(parent_idx, 0, new_count - 1)
            item.signals = [TxSignalItem(name=s.name, value=s.value, unit=s.unit) for s in new_sigs]
            self.endInsertRows()

            # Decode current raw_data to get actual signal values
            self._redecode_signals(row)

    def _redecode_signals(self, row: int):
        """Re-decode signals from raw_data after raw data changes."""
        if self._decoder is None:
            return
        item = self._items[row]
        if not item.signals:
            return
        decoded = self._decoder.decode(item.can_id, bytes(item.raw_data))
        if not decoded:
            return
        decoded_map = {d.name: d for d in decoded}
        for sig in item.signals:
            if sig.name in decoded_map:
                ds = decoded_map[sig.name]
                sig.value = ds.value

        # Notify signal rows changed
        parent_idx = self.index(row, 0)
        first = self.index(0, 0, parent_idx)
        last = self.index(len(item.signals) - 1, self.columnCount() - 1, parent_idx)
        self.dataChanged.emit(first, last)

    def _encode_signals(self, row: int):
        """Encode signal values back into raw_data."""
        if self._decoder is None:
            return
        item = self._items[row]
        signal_data = {sig.name: sig.value for sig in item.signals}
        encoded = self._decoder.encode(item.can_id, signal_data)
        if encoded is not None:
            item.raw_data = bytearray(encoded)
            item.length = len(item.raw_data)
            # Notify parent row data changed (raw_data column)
            top_left = self.index(row, 0)
            bottom_right = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right)

    # -- Public API --

    def add_empty_message(self, bus: int = 1):
        item = TxMessageItem(
            bus=bus,
            can_id=0,
            length=8,
            raw_data=bytearray(8),
            cycle_time_ms=100,
        )
        self.add_message(item)

    def add_message(self, item: TxMessageItem, resolve: bool = True):
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._resolve_from_db(item, override=resolve)
        self._items.append(item)
        self.endInsertRows()
        # Build signal children after insert
        self._rebuild_signals(row)

    def clear(self):
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    def remove_message(self, row: int):
        if 0 <= row < len(self._items):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._items.pop(row)
            self.endRemoveRows()

    def get_item(self, row: int) -> TxMessageItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def get_item_at(self, index: QModelIndex) -> TxMessageItem | None:
        """Get the message item for any index (top-level or child)."""
        if not index.isValid():
            return None
        if self._is_top_level(index):
            return self.get_item(index.row())
        parent_row = index.internalId() - 1
        return self.get_item(parent_row)

    def get_signal_at(self, index: QModelIndex) -> tuple[TxMessageItem, TxSignalItem] | None:
        """Get the signal item at an index, if it's a signal child row."""
        if not index.isValid() or self._is_top_level(index):
            return None
        parent_row = index.internalId() - 1
        if 0 <= parent_row < len(self._items):
            item = self._items[parent_row]
            if 0 <= index.row() < len(item.signals):
                return item, item.signals[index.row()]
        return None

    def get_all_symbols(self) -> list[str]:
        """Return all DBC symbol names for the dropdown."""
        if self._decoder is None:
            return []
        return [name for name, _ in self._decoder.get_all_symbols()]

    @property
    def items(self) -> list[TxMessageItem]:
        return self._items

    def clear_counts(self):
        """Reset all TX message counters to zero."""
        for item in self._items:
            item.count = 0
        if self._items:
            self.dataChanged.emit(
                self.index(0, 7),
                self.index(len(self._items) - 1, 7),
            )

    def increment_count(self, row: int):
        if 0 <= row < len(self._items):
            self._items[row].count += 1
            idx = self.index(row, 7)  # Count column
            self.dataChanged.emit(idx, idx)

    def increment_counts(self, counts: dict[int, int]):
        """Apply batched count deltas: {row: delta}."""
        min_row = None
        max_row = None
        for row, delta in counts.items():
            if 0 <= row < len(self._items):
                self._items[row].count += delta
                if min_row is None or row < min_row:
                    min_row = row
                if max_row is None or row > max_row:
                    max_row = row
        if min_row is not None:
            self.dataChanged.emit(
                self.index(min_row, 7),
                self.index(max_row, 7),
            )

    def refresh_signals(self):
        """Re-resolve symbols and rebuild signals after DBC load/remove."""
        for row, item in enumerate(self._items):
            self._resolve_from_db(item, override=False)
            self._rebuild_signals(row)
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, self.columnCount() - 1),
            )
