from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import cantools
from cantools.database.conversion import LinearConversion, NamedSignalConversion
from cantools.database.namedsignalvalue import NamedSignalValue
from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex

# Internal ID encoding for 3-level tree (File > Message > Signal):
#   File rows:    internalId = 0
#   Message rows: internalId = (file_row + 1) << 16
#   Signal rows:  internalId = ((file_row + 1) << 16) | (msg_row + 1)
_FILE_LEVEL = 0

COLUMNS = [
    "Name", "Bus", "ID (hex)", "DLC", "Cycle (ms)",
    "Start Bit", "Bit Length", "Byte Order", "Signed",
    "Factor", "Offset", "Min", "Max", "Unit", "Mux", "Values", "Comment",
]

# Column indices
COL_NAME = 0
COL_BUS = 1
COL_ID = 2
COL_DLC = 3
COL_CYCLE = 4
COL_START_BIT = 5
COL_BIT_LENGTH = 6
COL_BYTE_ORDER = 7
COL_SIGNED = 8
COL_FACTOR = 9
COL_OFFSET = 10
COL_MIN = 11
COL_MAX = 12
COL_UNIT = 13
COL_MUX = 14
COL_VALUES = 15
COL_COMMENT = 16

_FILE_EDITABLE = {COL_NAME, COL_BUS}
_MSG_EDITABLE = {COL_NAME, COL_ID, COL_DLC, COL_CYCLE, COL_COMMENT}
_SIG_EDITABLE = {COL_NAME, COL_START_BIT, COL_BIT_LENGTH, COL_BYTE_ORDER,
                 COL_SIGNED, COL_FACTOR, COL_OFFSET, COL_MIN, COL_MAX,
                 COL_UNIT, COL_MUX, COL_VALUES, COL_COMMENT}


@dataclass
class DbSignalItem:
    name: str = "NewSignal"
    start_bit: int = 0
    bit_length: int = 8
    byte_order: str = "little_endian"
    is_signed: bool = False
    factor: float = 1.0
    offset: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    unit: str = ""
    value_table: dict[int, str] = field(default_factory=dict)
    comment: str = ""
    is_multiplexer: bool = False
    multiplexer_ids: list[int] | None = None
    multiplexer_signal: str | None = None


@dataclass
class DbMessageItem:
    name: str = "NewMessage"
    can_id: int = 0x100
    dlc: int = 8
    cycle_time_ms: int = 0
    sender: str = ""
    comment: str = ""
    signals: list[DbSignalItem] = field(default_factory=list)


@dataclass
class DbFileItem:
    """Top-level grouping node representing a DBC/database file."""
    filename: str = "Untitled"
    bus: int = 1
    messages: list[DbMessageItem] = field(default_factory=list)
    source_path: str = ""  # original DBC path; empty for manually created


def _encode_id(file_row: int = -1, msg_row: int = -1) -> int:
    """Encode tree position into an internalId."""
    if file_row < 0:
        return _FILE_LEVEL
    if msg_row < 0:
        return (file_row + 1) << 16
    return ((file_row + 1) << 16) | (msg_row + 1)


def _decode_id(iid: int) -> tuple[int, int]:
    """Decode internalId into (file_row, msg_row). Returns -1 for levels above."""
    if iid == _FILE_LEVEL:
        return -1, -1
    file_row = (iid >> 16) - 1
    msg_part = iid & 0xFFFF
    msg_row = msg_part - 1 if msg_part > 0 else -1
    return file_row, msg_row


class DatabaseModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[DbFileItem] = []

    # -- QAbstractItemModel required overrides --

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            # File level
            return self.createIndex(row, column, _encode_id())
        file_row, msg_row = _decode_id(parent.internalId())
        if file_row < 0:
            # Parent is a file → creating message index
            return self.createIndex(row, column, _encode_id(parent.row()))
        if msg_row < 0:
            # Parent is a message → creating signal index
            return self.createIndex(row, column, _encode_id(file_row, parent.row()))
        return QModelIndex()

    def parent(self, index: QModelIndex):
        if not index.isValid():
            return QModelIndex()
        file_row, msg_row = _decode_id(index.internalId())
        if file_row < 0:
            # File level → no parent
            return QModelIndex()
        if msg_row < 0:
            # Message level → parent is file
            return self.createIndex(file_row, 0, _encode_id())
        # Signal level → parent is message
        return self.createIndex(msg_row, 0, _encode_id(file_row))

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._items)
        file_row, msg_row = _decode_id(parent.internalId())
        if file_row < 0:
            # File node → count messages
            if 0 <= parent.row() < len(self._items):
                return len(self._items[parent.row()].messages)
            return 0
        if msg_row < 0:
            # Message node → count signals
            if 0 <= file_row < len(self._items):
                msgs = self._items[file_row].messages
                if 0 <= parent.row() < len(msgs):
                    return len(msgs[parent.row()].signals)
            return 0
        return 0

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def _get_level(self, index: QModelIndex) -> int:
        """Return 0=file, 1=message, 2=signal."""
        file_row, msg_row = _decode_id(index.internalId())
        if file_row < 0:
            return 0
        if msg_row < 0:
            return 1
        return 2

    def _get_file_row(self, index: QModelIndex) -> int:
        """Return the file row for any index."""
        level = self._get_level(index)
        if level == 0:
            return index.row()
        file_row, msg_row = _decode_id(index.internalId())
        return file_row

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col = index.column()
        level = self._get_level(index)

        if level == 0:
            # File level
            if index.row() >= len(self._items):
                return None
            fitem = self._items[index.row()]
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                match col:
                    case 0: return fitem.filename
                    case 1: return fitem.bus
            return None

        if level == 1:
            # Message level
            file_row, _ = _decode_id(index.internalId())
            if file_row >= len(self._items):
                return None
            msgs = self._items[file_row].messages
            if index.row() >= len(msgs):
                return None
            item = msgs[index.row()]
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                match col:
                    case 0: return item.name
                    case 2: return f"0x{item.can_id:03X}"
                    case 3: return item.dlc
                    case 4: return item.cycle_time_ms
                    case 16: return item.comment
            return None

        # Signal level (level == 2)
        file_row, msg_row = _decode_id(index.internalId())
        if file_row >= len(self._items):
            return None
        msgs = self._items[file_row].messages
        if msg_row >= len(msgs):
            return None
        sigs = msgs[msg_row].signals
        if index.row() >= len(sigs):
            return None
        sig = sigs[index.row()]

        # Signed column: use CheckStateRole
        if col == COL_SIGNED and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if sig.is_signed else Qt.CheckState.Unchecked

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match col:
                case 0: return sig.name
                case 5: return sig.start_bit
                case 6: return sig.bit_length
                case 7: return sig.byte_order
                case 8: return None  # displayed via CheckStateRole
                case 9: return sig.factor
                case 10: return sig.offset
                case 11: return sig.minimum
                case 12: return sig.maximum
                case 13: return sig.unit
                case 14: return _format_mux(sig)
                case 15: return _format_value_table(sig.value_table)
                case 16: return sig.comment
        return None

    def flags(self, index: QModelIndex):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return flags
        col = index.column()
        level = self._get_level(index)
        if level == 0:
            if col in _FILE_EDITABLE:
                flags |= Qt.ItemFlag.ItemIsEditable
        elif level == 1:
            if col in _MSG_EDITABLE:
                flags |= Qt.ItemFlag.ItemIsEditable
        else:
            if col in _SIG_EDITABLE:
                if col == COL_SIGNED:
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                else:
                    flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        level = self._get_level(index)
        if level == 0:
            return self._set_file_data(index, value, role)
        if level == 1:
            return self._set_message_data(index, value, role)
        return self._set_signal_data(index, value, role)

    def _set_file_data(self, index: QModelIndex, value, role):
        if role != Qt.ItemDataRole.EditRole:
            return False
        row = index.row()
        if row >= len(self._items):
            return False
        fitem = self._items[row]
        col = index.column()
        match col:
            case 0:
                fitem.filename = str(value).strip() or fitem.filename
            case 1:
                try:
                    fitem.bus = max(1, int(value))
                except (ValueError, TypeError):
                    return False
            case _:
                return False
        self.dataChanged.emit(index, index)
        return True

    def _set_message_data(self, index: QModelIndex, value, role):
        if role != Qt.ItemDataRole.EditRole:
            return False
        file_row, _ = _decode_id(index.internalId())
        if file_row >= len(self._items):
            return False
        msgs = self._items[file_row].messages
        if index.row() >= len(msgs):
            return False
        item = msgs[index.row()]
        col = index.column()
        match col:
            case 0:
                item.name = str(value).strip() or item.name
            case 2:
                parsed = _parse_hex(str(value))
                if parsed is None:
                    return False
                item.can_id = parsed
            case 3:
                try:
                    item.dlc = max(0, min(64, int(value)))
                except (ValueError, TypeError):
                    return False
            case 4:
                try:
                    item.cycle_time_ms = max(0, int(value))
                except (ValueError, TypeError):
                    return False
            case 16:
                item.comment = str(value)
            case _:
                return False
        self.dataChanged.emit(index, index)
        return True

    def _set_signal_data(self, index: QModelIndex, value, role):
        file_row, msg_row = _decode_id(index.internalId())
        if file_row >= len(self._items):
            return False
        msgs = self._items[file_row].messages
        if msg_row >= len(msgs):
            return False
        sigs = msgs[msg_row].signals
        if index.row() >= len(sigs):
            return False
        sig = sigs[index.row()]
        col = index.column()

        if col == COL_SIGNED and role == Qt.ItemDataRole.CheckStateRole:
            sig.is_signed = Qt.CheckState(value) == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            return True

        if role != Qt.ItemDataRole.EditRole:
            return False

        match col:
            case 0:
                sig.name = str(value).strip() or sig.name
            case 5:
                try:
                    sig.start_bit = max(0, int(value))
                except (ValueError, TypeError):
                    return False
            case 6:
                try:
                    sig.bit_length = max(1, int(value))
                except (ValueError, TypeError):
                    return False
            case 7:
                if value in ("little_endian", "big_endian"):
                    sig.byte_order = value
                else:
                    return False
            case 9:
                try:
                    sig.factor = float(value)
                except (ValueError, TypeError):
                    return False
            case 10:
                try:
                    sig.offset = float(value)
                except (ValueError, TypeError):
                    return False
            case 11:
                try:
                    sig.minimum = float(value)
                except (ValueError, TypeError):
                    return False
            case 12:
                try:
                    sig.maximum = float(value)
                except (ValueError, TypeError):
                    return False
            case 13:
                sig.unit = str(value)
            case 14:
                parsed_mux = _parse_mux(str(value))
                if parsed_mux is None:
                    return False
                sig.is_multiplexer, sig.multiplexer_ids = parsed_mux
            case 15:
                parsed_vt = _parse_value_table(str(value))
                if parsed_vt is None:
                    return False
                sig.value_table = parsed_vt
            case 16:
                sig.comment = str(value)
            case _:
                return False
        self.dataChanged.emit(index, index)
        return True

    # -- Public API --

    def add_file(self, fitem: DbFileItem | None = None) -> int:
        """Add a new file-level grouping node."""
        if fitem is None:
            fitem = DbFileItem()
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(fitem)
        self.endInsertRows()
        return row

    def add_message(self, file_row: int = -1, item: DbMessageItem | None = None) -> int:
        """Add a message to the given file group. If file_row < 0, add to last file."""
        if not self._items:
            self.add_file()
        if file_row < 0:
            file_row = len(self._items) - 1
        if file_row < 0 or file_row >= len(self._items):
            return -1
        if item is None:
            # Auto-generate a unique CAN ID across all files
            existing = set()
            for f in self._items:
                for m in f.messages:
                    existing.add(m.can_id)
            new_id = 0x100
            while new_id in existing:
                new_id += 1
            item = DbMessageItem(can_id=new_id)
        fitem = self._items[file_row]
        parent_idx = self.index(file_row, 0)
        child_row = len(fitem.messages)
        self.beginInsertRows(parent_idx, child_row, child_row)
        fitem.messages.append(item)
        self.endInsertRows()
        return child_row

    def add_signal(self, file_row: int, msg_row: int, sig: DbSignalItem | None = None) -> int:
        if file_row < 0 or file_row >= len(self._items):
            return -1
        msgs = self._items[file_row].messages
        if msg_row < 0 or msg_row >= len(msgs):
            return -1
        if sig is None:
            sig = DbSignalItem()
        msg = msgs[msg_row]
        file_idx = self.index(file_row, 0)
        msg_idx = self.index(msg_row, 0, file_idx)
        child_row = len(msg.signals)
        self.beginInsertRows(msg_idx, child_row, child_row)
        msg.signals.append(sig)
        self.endInsertRows()
        return child_row

    def remove_row(self, index: QModelIndex):
        if not index.isValid():
            return
        level = self._get_level(index)
        if level == 0:
            row = index.row()
            if 0 <= row < len(self._items):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
        elif level == 1:
            file_row, _ = _decode_id(index.internalId())
            if 0 <= file_row < len(self._items):
                msgs = self._items[file_row].messages
                if 0 <= index.row() < len(msgs):
                    parent_idx = self.index(file_row, 0)
                    self.beginRemoveRows(parent_idx, index.row(), index.row())
                    msgs.pop(index.row())
                    self.endRemoveRows()
        else:
            file_row, msg_row = _decode_id(index.internalId())
            if 0 <= file_row < len(self._items):
                msgs = self._items[file_row].messages
                if 0 <= msg_row < len(msgs):
                    sigs = msgs[msg_row].signals
                    if 0 <= index.row() < len(sigs):
                        file_idx = self.index(file_row, 0)
                        msg_idx = self.index(msg_row, 0, file_idx)
                        self.beginRemoveRows(msg_idx, index.row(), index.row())
                        sigs.pop(index.row())
                        self.endRemoveRows()

    def remove_by_source_path(self, path: str) -> bool:
        """Remove the file-level item whose source_path matches path. Returns True if removed."""
        from pathlib import Path as _Path
        try:
            norm = _Path(path).resolve()
        except Exception:
            norm = _Path(path)
        for row, item in enumerate(self._items):
            try:
                item_norm = _Path(item.source_path).resolve()
            except Exception:
                item_norm = _Path(item.source_path)
            if item_norm == norm:
                idx = self.index(row, 0)
                self.remove_row(idx)
                return True
        return False

    def get_message_row(self, index: QModelIndex) -> tuple[int, int]:
        """Return (file_row, msg_row) for any index. Returns (-1, -1) if not found."""
        if not index.isValid():
            return -1, -1
        level = self._get_level(index)
        if level == 0:
            return -1, -1
        if level == 1:
            file_row, _ = _decode_id(index.internalId())
            return file_row, index.row()
        # Signal level
        file_row, msg_row = _decode_id(index.internalId())
        return file_row, msg_row

    def get_signal(self, index: QModelIndex) -> tuple[DbMessageItem, DbSignalItem] | None:
        """Return (message, signal) if index is a signal child row."""
        if not index.isValid() or self._get_level(index) != 2:
            return None
        file_row, msg_row = _decode_id(index.internalId())
        if 0 <= file_row < len(self._items):
            msgs = self._items[file_row].messages
            if 0 <= msg_row < len(msgs):
                msg = msgs[msg_row]
                if 0 <= index.row() < len(msg.signals):
                    return msg, msg.signals[index.row()]
        return None

    @property
    def items(self) -> list[DbFileItem]:
        return self._items

    # -- Serialization --

    def to_dict(self, manual_only: bool = False) -> list[dict]:
        result = []
        for fitem in self._items:
            if manual_only and fitem.source_path:
                continue
            messages = []
            for msg in fitem.messages:
                signals = []
                for sig in msg.signals:
                    sig_dict = {
                        "name": sig.name,
                        "start_bit": sig.start_bit,
                        "bit_length": sig.bit_length,
                        "byte_order": sig.byte_order,
                        "is_signed": sig.is_signed,
                        "factor": sig.factor,
                        "offset": sig.offset,
                        "minimum": sig.minimum,
                        "maximum": sig.maximum,
                        "unit": sig.unit,
                        "comment": sig.comment,
                    }
                    if sig.value_table:
                        sig_dict["value_table"] = {str(k): v for k, v in sig.value_table.items()}
                    if sig.is_multiplexer:
                        sig_dict["is_multiplexer"] = True
                    if sig.multiplexer_ids is not None:
                        sig_dict["multiplexer_ids"] = sig.multiplexer_ids
                    if sig.multiplexer_signal is not None:
                        sig_dict["multiplexer_signal"] = sig.multiplexer_signal
                    signals.append(sig_dict)
                messages.append({
                    "name": msg.name,
                    "id": f"0x{msg.can_id:03X}",
                    "dlc": msg.dlc,
                    "cycle_time": msg.cycle_time_ms,
                    "sender": msg.sender,
                    "comment": msg.comment,
                    "signals": signals,
                })
            result.append({
                "filename": fitem.filename,
                "bus": fitem.bus,
                "messages": messages,
            })
        return result

    def from_dict(self, data: list[dict]):
        self.beginResetModel()
        self._items.clear()
        for file_data in data:
            self._items.append(_file_item_from_dict(file_data))
        self.endResetModel()

    def append_from_dict(self, data: list[dict]):
        """Append file entries from dict without clearing existing data."""
        if not data:
            return
        first = len(self._items)
        last = first + len(data) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        for file_data in data:
            self._items.append(_file_item_from_dict(file_data))
        self.endInsertRows()

    # -- DBC import/export --

    def import_from_cantools(self, db: cantools.database.Database,
                             filename: str = "Untitled", bus: int = 1,
                             source_path: str = "",
                             append: bool = True):
        """Import a cantools database as a new file-level node."""
        messages = []
        for msg in db.messages:
            signals = []
            for sig in msg.signals:
                byte_order = "little_endian" if sig.byte_order == "little_endian" else "big_endian"
                vt = {}
                if sig.choices:
                    vt = {int(k): str(v) for k, v in sig.choices.items()}
                mux_ids = None
                if sig.multiplexer_ids is not None:
                    mux_ids = list(sig.multiplexer_ids)
                signals.append(DbSignalItem(
                    name=sig.name,
                    start_bit=sig.start,
                    bit_length=sig.length,
                    byte_order=byte_order,
                    is_signed=sig.is_signed,
                    factor=float(sig.scale),
                    offset=float(sig.offset),
                    minimum=float(sig.minimum or 0),
                    maximum=float(sig.maximum or 0),
                    unit=sig.unit or "",
                    value_table=vt,
                    comment=sig.comment or "",
                    is_multiplexer=sig.is_multiplexer,
                    multiplexer_ids=mux_ids,
                    multiplexer_signal=sig.multiplexer_signal,
                ))
            sender = ""
            if msg.senders:
                sender = msg.senders[0]
            messages.append(DbMessageItem(
                name=msg.name,
                can_id=msg.frame_id,
                dlc=msg.length,
                cycle_time_ms=msg.cycle_time or 0,
                sender=sender,
                comment=msg.comment or "",
                signals=signals,
            ))

        fitem = DbFileItem(filename=filename, bus=bus, messages=messages,
                           source_path=source_path)

        if append:
            row = len(self._items)
            self.beginInsertRows(QModelIndex(), row, row)
            self._items.append(fitem)
            self.endInsertRows()
        else:
            self.beginResetModel()
            self._items = [fitem]
            self.endResetModel()

    def export_to_cantools(self) -> cantools.database.Database:
        """Export all files' messages into a single cantools Database."""
        ct_messages = []
        for fitem in self._items:
            for msg in fitem.messages:
                signals = []
                for sig in msg.signals:
                    if sig.value_table:
                        choices = OrderedDict(
                            (k, NamedSignalValue(k, v))
                            for k, v in sorted(sig.value_table.items())
                        )
                        conversion = NamedSignalConversion(
                            scale=sig.factor,
                            offset=sig.offset,
                            choices=choices,
                            is_float=False,
                        )
                    else:
                        conversion = LinearConversion(
                            scale=sig.factor,
                            offset=sig.offset,
                            is_float=False,
                        )
                    signals.append(cantools.database.Signal(
                        name=sig.name,
                        start=sig.start_bit,
                        length=sig.bit_length,
                        byte_order=sig.byte_order,
                        is_signed=sig.is_signed,
                        conversion=conversion,
                        minimum=sig.minimum,
                        maximum=sig.maximum,
                        unit=sig.unit,
                        comment=sig.comment or None,
                        is_multiplexer=sig.is_multiplexer,
                        multiplexer_ids=sig.multiplexer_ids,
                        multiplexer_signal=sig.multiplexer_signal,
                    ))
                ct_messages.append(cantools.database.Message(
                    frame_id=msg.can_id,
                    name=msg.name,
                    length=msg.dlc,
                    signals=signals,
                    comment=msg.comment or None,
                    cycle_time=msg.cycle_time_ms or None,
                    senders=[msg.sender] if msg.sender else None,
                ))
        return cantools.database.Database(messages=ct_messages)


def _file_item_from_dict(file_data: dict) -> DbFileItem:
    """Parse a single file-level dict into a DbFileItem."""
    messages = []
    for msg_data in file_data.get("messages", []):
        can_id = _parse_hex(msg_data.get("id", "0x000"))
        if can_id is None:
            can_id = 0
        signals = []
        for sd in msg_data.get("signals", []):
            vt_raw = sd.get("value_table", {})
            vt = {int(k): str(v) for k, v in vt_raw.items()} if vt_raw else {}
            signals.append(DbSignalItem(
                name=sd.get("name", ""),
                start_bit=sd.get("start_bit", 0),
                bit_length=sd.get("bit_length", 8),
                byte_order=sd.get("byte_order", "little_endian"),
                is_signed=sd.get("is_signed", False),
                factor=sd.get("factor", 1.0),
                offset=sd.get("offset", 0.0),
                minimum=sd.get("minimum", 0.0),
                maximum=sd.get("maximum", 0.0),
                unit=sd.get("unit", ""),
                value_table=vt,
                comment=sd.get("comment", ""),
                is_multiplexer=sd.get("is_multiplexer", False),
                multiplexer_ids=sd.get("multiplexer_ids"),
                multiplexer_signal=sd.get("multiplexer_signal"),
            ))
        messages.append(DbMessageItem(
            name=msg_data.get("name", "NewMessage"),
            can_id=can_id,
            dlc=msg_data.get("dlc", 8),
            cycle_time_ms=msg_data.get("cycle_time", 0),
            sender=msg_data.get("sender", ""),
            comment=msg_data.get("comment", ""),
            signals=signals,
        ))
    return DbFileItem(
        filename=file_data.get("filename", "Untitled"),
        bus=file_data.get("bus", 1),
        messages=messages,
    )


def _format_value_table(vt: dict[int, str]) -> str:
    """Format a value table as '0=Off, 1=On, 2=Error'."""
    if not vt:
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(vt.items()))


def _parse_value_table(text: str) -> dict[int, str] | None:
    """Parse '0=Off, 1=On, 2=Error' into {0: 'Off', 1: 'On', 2: 'Error'}.

    Returns empty dict for empty string, None on parse error.
    """
    text = text.strip()
    if not text:
        return {}
    result = {}
    for part in text.split(","):
        part = part.strip()
        if "=" not in part:
            return None
        key_str, val_str = part.split("=", 1)
        try:
            result[int(key_str.strip())] = val_str.strip()
        except ValueError:
            return None
    return result


def _format_mux(sig: DbSignalItem) -> str:
    """Format mux info: 'M' for selector, 'm0', 'm0,1,2' for muxed, '' for normal."""
    if sig.is_multiplexer:
        return "M"
    if sig.multiplexer_ids is not None:
        return "m" + ",".join(str(i) for i in sig.multiplexer_ids)
    return ""


def _parse_mux(text: str) -> tuple[bool, list[int] | None] | None:
    """Parse mux string. Returns (is_multiplexer, multiplexer_ids) or None on error."""
    text = text.strip()
    if not text:
        return False, None
    if text == "M":
        return True, None
    if text.startswith("m"):
        try:
            ids = [int(x.strip()) for x in text[1:].split(",")]
            return False, ids
        except ValueError:
            return None
    return None


def _parse_hex(text: str) -> int | None:
    text = text.strip()
    if text.startswith("0x") or text.startswith("0X"):
        text = text[2:]
    try:
        return int(text, 16)
    except ValueError:
        return None
