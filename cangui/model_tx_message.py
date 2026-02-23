"""Qt tree-table model for outgoing (TX) CAN messages and their decoded signals.

The model uses a two-level tree:

- Top-level rows represent TxMessageItem objects (one per configured TX message).
- Child rows beneath each message represent TxSignalItem objects decoded from
  the DBC database via SignalDecoder.

Internal pointer encoding (same scheme as RxMessageModel):

- Top-level rows: ``internalId == 0`` (``_TOP_LEVEL``).
- Signal child rows: ``internalId == parent_row + 1``.

Editing a signal's value field re-encodes all signals via SignalDecoder and
redraws the parent row's hex data column so the two views stay in sync.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QTimer, QByteArray, QMimeData

from cangui.signal_decoder import SignalDecoder

# Internal ID encoding (same scheme as RxMessageModel):
#   Top-level (message) rows: internalId = 0
#   Child (signal) rows:      internalId = parent_row + 1
_TOP_LEVEL = 0


@dataclass
class TxSignalItem:
    """Mutable state for a single DBC signal belonging to a TX message.

    Attributes:
        name: Signal name as defined in the DBC database.
        value: Current physical value; may be a float, int, or enum string.
        unit: Physical unit string, e.g. ``"km/h"``.
        is_multiplexer: True when this signal is the multiplexer selector (``[M]``).
        multiplexer_ids: List of mux IDs this signal belongs to (``[m…]``),
            or ``None`` for non-multiplexed signals.
    """

    name: str = ""
    value: object = 0  # float, int, or str
    unit: str = ""
    is_multiplexer: bool = False
    multiplexer_ids: list[int] | None = None
    choices: list[str] | None = None  # named values from value table; drives combobox editor


@dataclass
class TxMessageItem:
    """Mutable configuration for a single TX CAN message row.

    Attributes:
        bus: CAN channel number (1-based) on which to transmit.
        can_id: CAN arbitration ID.
        is_extended_id: True for 29-bit extended IDs.
        frame_type: Frame type string, e.g. ``"Data"`` or ``"Remote"``.
        length: Payload byte length (0–64).
        symbol: DBC message name resolved from the database, if available.
        raw_data: Current payload as a mutable bytearray.
        cycle_time_ms: Periodic transmission interval in milliseconds.
        cycle_enabled: True when periodic transmission is active.
        count: Number of times this message has been transmitted.
        trigger: Trigger mode label, e.g. ``"Time"`` or ``"Wait"``.
        creator: Origin label, e.g. ``"User"`` or a script plugin name.
        signals: Decoded signal children; rebuilt whenever the CAN-ID changes.
        last_sent_data: Most-recently transmitted payload supplied by a script
            plugin for display purposes only; does not affect raw_data.
    """

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
    last_sent_data: bytes | None = None  # set by script plugin feedback; display-only


COLUMNS = ["Bus", "ID (hex)", "Ext", "Type", "Length", "Symbol",
           "Data (hex)", "Cycle Time", "Count", "Trigger", "Creator"]

_DRAG_MIME = "application/x-cangui-rows"


def _mux_prefix(sig: TxSignalItem) -> str:
    """Return the multiplexing role prefix string for a signal's display name.

    Args:
        sig: The signal whose multiplexing role is inspected.
    Returns:
        ``"[M] "`` for the mux selector, ``"[m<ids>] "`` for muxed signals,
        or ``""`` for plain signals.
    """
    if sig.is_multiplexer:
        return "[M] "
    if sig.multiplexer_ids is not None:
        return "[m" + ",".join(str(i) for i in sig.multiplexer_ids) + "] "
    return ""


class TxMessageModel(QAbstractItemModel):
    """Qt tree-table model managing transmit CAN messages and their DBC signals.

    Top-level rows are TxMessageItem objects; each may have TxSignalItem children
    decoded from the DBC database.  Editing a signal value re-encodes via
    SignalDecoder so that raw_data always reflects the current physical values.

    The internal-pointer scheme encodes parent row membership:

    - ``internalId == 0`` — top-level message row.
    - ``internalId == parent_row + 1`` — signal child of ``parent_row``.
    """

    def __init__(self, parent=None):
        """Initialise the model with an empty message list.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._items: list[TxMessageItem] = []
        self._decoder: SignalDecoder | None = None
        self._pending_last_sent: dict[int, bytes] = {}

    def set_decoder(self, decoder: SignalDecoder):
        """Attach the signal decoder used for DBC look-ups and encoding.

        Args:
            decoder: Fully initialised SignalDecoder instance.
        """
        self._decoder = decoder

    # -- QAbstractItemModel required overrides --

    def index(self, row, column, parent=QModelIndex()):
        """Return a model index for the given row/column under *parent*.

        Top-level message rows use ``internalId == _TOP_LEVEL``; signal child
        rows encode their parent row as ``internalId = parent_row + 1``.

        Args:
            row: Zero-based child row.
            column: Zero-based column.
            parent: Parent index; invalid means the root (message level).

        Returns:
            Valid :class:`QModelIndex`, or invalid if out of range.
        """
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, _TOP_LEVEL)
        else:
            return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index: QModelIndex):
        """Return the parent index of a signal child row.

        Args:
            index: Index whose parent should be found.

        Returns:
            The parent message :class:`QModelIndex` for signal rows, or an
            invalid index for top-level message rows.
        """
        if not index.isValid():
            return QModelIndex()
        ptr = index.internalId()
        if ptr == _TOP_LEVEL:
            return QModelIndex()
        parent_row = ptr - 1
        return self.createIndex(parent_row, 0, _TOP_LEVEL)

    def rowCount(self, parent=QModelIndex()):
        """Return the number of rows under *parent*.

        Args:
            parent: Invalid index → number of top-level messages.
                Valid top-level index → number of decoded signals for that message.

        Returns:
            Row count, or 0 for signal-level parents.
        """
        if not parent.isValid():
            return len(self._items)
        if parent.internalId() == _TOP_LEVEL and 0 <= parent.row() < len(self._items):
            return len(self._items[parent.row()].signals)
        return 0

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed number of columns defined by ``COLUMNS``.

        Args:
            parent: Unused.

        Returns:
            Total number of columns (``len(COLUMNS)``).
        """
        return len(COLUMNS)

    def _is_top_level(self, index: QModelIndex) -> bool:
        """Return ``True`` if *index* refers to a top-level message row.

        Args:
            index: The model index to check.

        Returns:
            ``True`` for message rows, ``False`` for signal child rows.
        """
        return index.internalId() == _TOP_LEVEL

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for the horizontal header.

        Args:
            section: Zero-based column index.
            orientation: Only horizontal headers are populated.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Column name string, or ``None`` for unhandled orientation/role.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display, edit, or check-state data for the given cell.

        For top-level rows, the Extended (col 2) and Cycle-Enable (col 7)
        columns use ``CheckStateRole``; all other supported columns use
        ``DisplayRole``/``EditRole``.  Signal child rows expose only the Name
        (col 5) and Value (col 6) columns.

        Args:
            index: Cell position in the model.
            role: Qt data role determining which aspect of the cell is returned.

        Returns:
            Cell value, or ``None`` for unhandled roles or unsupported columns.
        """
        if not index.isValid():
            return None
        col = index.column()

        if self._is_top_level(index):
            if index.row() >= len(self._items):
                return None
            item = self._items[index.row()]

            if role == Qt.ItemDataRole.CheckStateRole:
                if col == 2:
                    return Qt.CheckState.Checked if item.is_extended_id else Qt.CheckState.Unchecked
                if col == 7:
                    return Qt.CheckState.Checked if item.cycle_enabled else Qt.CheckState.Unchecked

            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                match col:
                    case 0: return item.bus
                    case 1:
                        if item.is_extended_id:
                            return f"{item.can_id:08X}"
                        return f"{item.can_id:03X}"
                    case 2: return None  # checkbox only
                    case 3: return item.frame_type
                    case 4: return item.length
                    case 5: return item.symbol
                    case 6:
                        display = item.last_sent_data if item.last_sent_data is not None else item.raw_data
                        return " ".join(f"{b:02X}" for b in display[:item.length])
                    case 7: return item.cycle_time_ms
                    case 8: return item.count
                    case 9: return "Time" if item.cycle_enabled else "Wait"
                    case 10: return item.creator
        else:
            # Signal child row
            parent_row = index.internalId() - 1
            if parent_row >= len(self._items):
                return None
            sigs = self._items[parent_row].signals
            if index.row() >= len(sigs):
                return None
            sig = sigs[index.row()]

            if role == Qt.ItemDataRole.UserRole:
                # Return the choices list for the combobox delegate (col 6 only)
                if col == 6:
                    return sig.choices
                return None

            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                match col:
                    case 5: return _mux_prefix(sig) + sig.name
                    case 6:
                        if sig.unit:
                            return f"{sig.value} {sig.unit}"
                        return str(sig.value)

        return None

    def flags(self, index: QModelIndex):
        """Return interaction flags for the given cell.

        Top-level columns 2 (Ext) and 7 (Cycle Enable) are checkable.  Columns
        0, 1, 4, 5, 6, 7 are inline-editable.  Signal child column 6 (Value)
        is editable.

        Args:
            index: Cell position in the model.

        Returns:
            Combined :class:`Qt.ItemFlag` value for the cell.
        """
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return flags

        if self._is_top_level(index):
            if index.column() in (2, 7):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            if index.column() in (0, 1, 4, 5, 6, 7):
                flags |= Qt.ItemFlag.ItemIsEditable
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        else:
            # Signal child rows: data column (6) is editable; not draggable
            if index.column() == 6:
                flags |= Qt.ItemFlag.ItemIsEditable

        flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def supportedDropActions(self):
        """Return MoveAction as the only supported drop action."""
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        """Return the list of MIME types supported for drag operations."""
        return [_DRAG_MIME]

    def mimeData(self, indexes):
        """Encode top-level message row indices into MIME data."""
        mime = QMimeData()
        rows = sorted({idx.row() for idx in indexes
                       if idx.isValid() and self._is_top_level(idx)})
        mime.setData(_DRAG_MIME, QByteArray(b",".join(str(r).encode() for r in rows)))
        return mime

    def dropMimeData(self, data, action, row, col, parent):
        """Handle a drop event by reordering top-level message rows."""
        if not data.hasFormat(_DRAG_MIME):
            return False
        raw = bytes(data.data(_DRAG_MIME))
        if not raw:
            return False
        src_rows = [int(r) for r in raw.split(b",") if r]
        dest = row if row >= 0 else len(self._items)
        self.beginResetModel()
        offset = 0
        for src in sorted(src_rows):
            effective_src = src - offset
            effective_dest = dest - offset if dest > src else dest
            if effective_src == effective_dest:
                continue
            item = self._items.pop(effective_src)
            self._items.insert(effective_dest, item)
            if dest > src:
                offset += 1
        self.endResetModel()
        return True

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        """Dispatch a write to the appropriate top-level or signal handler.

        Args:
            index: Cell position to update.
            value: New value to store.
            role: ``EditRole`` or ``CheckStateRole``.

        Returns:
            ``True`` if the write was accepted, ``False`` otherwise.
        """
        if not index.isValid():
            return False

        if self._is_top_level(index):
            return self._set_top_level_data(index, value, role)
        else:
            return self._set_signal_data(index, value, role)

    def _set_top_level_data(self, index, value, role):
        """Handle writes to top-level message row cells.

        Processes checkboxes for the Ext and Cycle Enable columns, and inline
        edits for Bus, CAN-ID, DLC, Symbol, Data, and Cycle Time.  CAN-ID and
        Symbol changes trigger a DBC look-up and signal rebuild.

        Args:
            index: Top-level cell position.
            value: New value to store.
            role: ``EditRole`` or ``CheckStateRole``.

        Returns:
            ``True`` if the write was accepted, ``False`` otherwise.
        """
        if index.row() >= len(self._items):
            return False
        item = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole:
            if col == 2:
                item.is_extended_id = Qt.CheckState(value) == Qt.CheckState.Checked
                # ID display format changes too — notify cols 1 and 2
                id_idx = self.index(index.row(), 1)
                self.dataChanged.emit(id_idx, index)
                return True
            if col == 7:
                item.cycle_enabled = Qt.CheckState(value) == Qt.CheckState.Checked
                # Notify both the checkbox column and the Trigger column
                trigger_idx = self.index(index.row(), 9)
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
                    # Auto-extend when the ID requires more than 11 bits
                    if item.can_id > 0x7FF:
                        item.is_extended_id = True
                    item.last_sent_data = None
                    self._resolve_from_db(item)
                    self._rebuild_signals(index.row())
                case 4:
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
                case 5:
                    name = str(value).strip()
                    if not name or self._decoder is None:
                        return False
                    arb_id = self._decoder.get_id_by_symbol(name)
                    if arb_id is None:
                        return False
                    item.can_id = arb_id
                    item.last_sent_data = None
                    self._resolve_from_db(item)
                    self._rebuild_signals(index.row())
                case 6:
                    try:
                        data = bytes.fromhex(str(value).replace(" ", ""))
                        item.raw_data = bytearray(data)
                        item.length = len(data)
                        item.last_sent_data = None
                    except ValueError:
                        return False
                    self._redecode_signals(index.row())
                case 7:
                    try:
                        item.cycle_time_ms = int(value)
                    except (ValueError, TypeError):
                        return False
                case _:
                    return False
            # CAN-ID / Symbol change updates the whole row (symbol, length, data, cycle)
            if col in (1, 5):
                left = self.index(index.row(), 0)
                right = self.index(index.row(), self.columnCount() - 1)
                self.dataChanged.emit(left, right)
            else:
                self.dataChanged.emit(index, index)
            return True
        return False

    def _set_signal_data(self, index, value, role):
        """Handle writes to signal child row cells (column 6 only).

        Parses the entered value, strips a trailing unit string if present,
        updates the signal, re-encodes all active signals into ``raw_data``,
        then redecodes so displayed values snap to quantized DBC values.

        Args:
            index: Signal child cell position (must be column 6).
            value: New value string (may include unit suffix).
            role: Must be ``EditRole``; other roles are rejected.

        Returns:
            ``True`` if the write was accepted, ``False`` otherwise.
        """
        if role != Qt.ItemDataRole.EditRole or index.column() != 6:
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
        item.last_sent_data = None  # user edited a signal; clear plugin display

        # Re-encode all signals back into raw_data, then redecode
        # so displayed values snap to the actual quantized values
        self._encode_signals(parent_row)
        self._redecode_signals(parent_row)
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
            # Encode initial signal values into raw_data.
            # For multiplexed messages, only include the mux selector,
            # non-muxed signals, and the default mux group (id 0).
            sigs = self._decoder.get_signals_for_id(item.can_id)
            mux_value = 0
            signal_data = {}
            for s in sigs:
                if s.is_multiplexer:
                    signal_data[s.name] = mux_value
                elif s.multiplexer_ids is None:
                    signal_data[s.name] = s.value
                elif mux_value in s.multiplexer_ids:
                    signal_data[s.name] = s.value
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
            item.signals = [
                TxSignalItem(
                    name=s.name, value=s.value, unit=s.unit,
                    is_multiplexer=s.is_multiplexer, multiplexer_ids=s.multiplexer_ids,
                    choices=s.choices,
                )
                for s in new_sigs
            ]
            self.endInsertRows()

            # Decode current raw_data to get actual signal values
            self._redecode_signals(row)

    def _redecode_signals(self, row: int, data: bytes | None = None):
        """Re-decode signals from raw_data (or given data) after raw data changes."""
        if self._decoder is None:
            return
        item = self._items[row]
        if not item.signals:
            return
        decode_data = data if data is not None else bytes(item.raw_data)
        decoded = self._decoder.decode(item.can_id, decode_data)
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
        """Encode signal values back into raw_data.

        For multiplexed messages, only include signals for the currently active
        mux group (based on the mux selector's value) plus non-muxed signals.
        """
        if self._decoder is None:
            return
        item = self._items[row]

        # Find the mux selector value (if any)
        mux_value = None
        for sig in item.signals:
            if sig.is_multiplexer:
                try:
                    mux_value = int(sig.value)
                except (ValueError, TypeError):
                    mux_value = 0
                break

        # Build signal dict, filtering by active mux group
        signal_data = {}
        for sig in item.signals:
            if sig.multiplexer_ids is not None and mux_value is not None:
                if mux_value not in sig.multiplexer_ids:
                    continue
            signal_data[sig.name] = sig.value

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
        """Append a new blank TX message with 8-byte zero payload.

        Args:
            bus: CAN channel number (1-based) for the new message.
        """
        item = TxMessageItem(
            bus=bus,
            can_id=0,
            length=8,
            raw_data=bytearray(8),
            cycle_time_ms=100,
        )
        self.add_message(item)

    def add_message(self, item: TxMessageItem, resolve: bool = True):
        """Append a TxMessageItem to the model and rebuild its signal children.

        Args:
            item: :class:`TxMessageItem` to add.
            resolve: When ``True``, apply DBC info (DLC, cycle time, initial
                payload) to the item before inserting.  Set to ``False`` when
                adding a pre-configured item that should not be overwritten.
        """
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._resolve_from_db(item, override=resolve)
        self._items.append(item)
        self.endInsertRows()
        # Build signal children after insert
        self._rebuild_signals(row)

    def clear(self):
        """Remove all TX messages and reset the model."""
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    def remove_message(self, row: int):
        """Remove the message at the given row index.

        Does nothing if *row* is out of range.

        Args:
            row: Zero-based row index of the message to remove.
        """
        if 0 <= row < len(self._items):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._items.pop(row)
            self.endRemoveRows()

    def move_up(self, row: int):
        """Move the message at *row* one position up.

        Does nothing if *row* is already at the top or out of range.

        Args:
            row: Zero-based row index to move.
        """
        if row <= 0 or row >= len(self._items):
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
        self._items.insert(row - 1, self._items.pop(row))
        self.endMoveRows()

    def move_down(self, row: int):
        """Move the message at *row* one position down.

        Does nothing if *row* is already at the bottom or out of range.

        Args:
            row: Zero-based row index to move.
        """
        if row < 0 or row >= len(self._items) - 1:
            return
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
        self._items.insert(row + 1, self._items.pop(row))
        self.endMoveRows()

    def get_item(self, row: int) -> TxMessageItem | None:
        """Return the :class:`TxMessageItem` at *row*, or ``None`` if out of range.

        Args:
            row: Zero-based row index.

        Returns:
            :class:`TxMessageItem` or ``None``.
        """
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
        """Direct reference to the internal list of TX message items.

        Returns:
            Mutable list of :class:`TxMessageItem` objects in display order.
        """
        return self._items

    def clear_counts(self):
        """Reset all TX message counters to zero."""
        for item in self._items:
            item.count = 0
        if self._items:
            self.dataChanged.emit(
                self.index(0, 8),
                self.index(len(self._items) - 1, 8),
            )

    def update_last_sent(self, row: int, data: bytes):
        """Coalesce script-plugin display updates; flushed at most every 50 ms."""
        if 0 <= row < len(self._items):
            was_empty = not self._pending_last_sent
            self._pending_last_sent[row] = data
            if was_empty:
                QTimer.singleShot(50, self._flush_last_sent)

    def _flush_last_sent(self):
        """Apply coalesced ``last_sent_data`` updates and redecode affected rows.

        Drains ``_pending_last_sent``, writes the latest payload to each item,
        emits ``dataChanged`` for the Data column, and redecodes signal children
        so displayed values reflect what was actually transmitted.  If new
        updates arrived during the flush another one-shot timer is scheduled.
        """
        pending = self._pending_last_sent
        self._pending_last_sent = {}
        for row, data in pending.items():
            if 0 <= row < len(self._items):
                self._items[row].last_sent_data = data
                idx = self.index(row, 6)
                self.dataChanged.emit(idx, idx)
                self._redecode_signals(row, data)
        if self._pending_last_sent:
            QTimer.singleShot(50, self._flush_last_sent)

    def increment_count(self, row: int):
        """Increment the send counter for *row* and emit ``dataChanged``.

        Args:
            row: Zero-based row index of the message whose counter to increment.
        """
        if 0 <= row < len(self._items):
            self._items[row].count += 1
            idx = self.index(row, 8)  # Count column
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
                self.index(min_row, 8),
                self.index(max_row, 8),
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
