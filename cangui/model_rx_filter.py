"""RX filter model for gating incoming CAN messages by CAN-ID range and bus.

Rules are evaluated top-to-bottom in a first-match fashion. Each rule specifies
a CAN-ID range (id_from..id_to), an optional bus number, and an action (Pass or
Drop). If no rule matches a message, the message is passed through by default.

The model is persisted as a list of plain dicts via to_dicts()/from_dicts() for
inclusion in project files.
"""

from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal, QByteArray, QMimeData


class FilterAction(Enum):
    """Action to apply when a filter rule matches an incoming CAN message."""

    PASS = "Pass"
    DROP = "Drop"


@dataclass
class RxFilterRule:
    """A single filter rule that matches CAN messages by ID range and bus.

    Attributes:
        enabled: Whether this rule is active during evaluation.
        action: The action to take when a message matches (Pass or Drop).
        id_from: Inclusive lower bound of the CAN arbitration-ID range.
        id_to: Inclusive upper bound of the CAN arbitration-ID range.
        bus: Bus number to restrict this rule to. 0 means any bus.
        name: Human-readable label shown in the filter table.
    """

    enabled: bool = True
    action: FilterAction = FilterAction.PASS
    id_from: int = 0x000
    id_to: int = 0x7FF
    bus: int = 0  # 0 = any bus
    name: str = ""

    def matches(self, arb_id: int, bus: int) -> bool:
        """Return True if the given arbitration ID and bus fall within this rule.

        Args:
            arb_id: The CAN arbitration ID of the incoming message.
            bus: The bus number on which the message was received.

        Returns:
            True when the message falls within id_from..id_to and the bus
            constraint is satisfied (rule.bus == 0 or rule.bus == bus).
        """
        if self.bus != 0 and self.bus != bus:
            return False
        return self.id_from <= arb_id <= self.id_to


COLUMNS = ["", "Action", "Name", "CAN-ID From", "CAN-ID To", "Bus"]

_DRAG_MIME = "application/x-cangui-rows"


class RxFilterModel(QAbstractTableModel):
    """First-match filter table for incoming CAN messages.

    Rules are evaluated top-to-bottom. The first enabled rule whose
    CAN-ID range and bus match determines whether the message is
    passed or dropped.  If no rule matches the message is passed.

    The model is initialised with two default rules that pass all
    standard-frame (0x000–0x7FF) and extended-frame (0x800–0x1FFFFFFF)
    messages respectively.

    Signals:
        filters_changed: Emitted after any structural or value change to the
            rule list so that consumers can reapply the filter.
    """

    filters_changed = Signal()

    def __init__(self, parent=None):
        """Initialise the model with two default pass-all rules.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._rules: list[RxFilterRule] = [
            RxFilterRule(name="Standard", action=FilterAction.PASS,
                         id_from=0x000, id_to=0x7FF),
            RxFilterRule(name="Extended", action=FilterAction.PASS,
                         id_from=0x800, id_to=0x1FFFFFFF),
        ]

    # -- Qt model interface --

    def rowCount(self, parent=QModelIndex()) -> int:
        """Return the number of filter rules.

        Args:
            parent: Must be an invalid index for a flat table model.

        Returns:
            Number of rules, or 0 when parent is valid (no child rows).
        """
        if parent.isValid():
            return 0
        return len(self._rules)

    def columnCount(self, parent=QModelIndex()) -> int:
        """Return the number of columns in the filter table.

        Args:
            parent: Unused for a flat table model.

        Returns:
            The fixed number of columns defined by COLUMNS.
        """
        return len(COLUMNS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for horizontal orientation.

        Args:
            section: Column index.
            orientation: Qt.Orientation; only Horizontal is handled.
            role: ItemDataRole; only DisplayRole returns a value.

        Returns:
            The column header string, or None for unsupported roles/orientations.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display, edit, or check-state data for a cell.

        Column mapping:
            0: CheckStateRole — enabled flag.
            1: action (Pass/Drop).
            2: rule name.
            3: id_from — displayed as ``0x…``, edited as bare hex.
            4: id_to   — displayed as ``0x…``, edited as bare hex.
            5: bus number — displayed as "Any" when 0, edited as integer.

        Args:
            index: The model index identifying the cell.
            role: The data role being queried.

        Returns:
            The requested cell value, or None for unsupported roles or invalid
            indices.
        """
        if not index.isValid():
            return None
        rule = self._rules[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match col:
                case 1:
                    return rule.action.value
                case 2:
                    return rule.name
                case 3:
                    if role == Qt.ItemDataRole.EditRole:
                        return f"{rule.id_from:03X}"
                    return f"0x{rule.id_from:03X}"
                case 4:
                    if role == Qt.ItemDataRole.EditRole:
                        return f"{rule.id_to:03X}"
                    return f"0x{rule.id_to:03X}"
                case 5:
                    if rule.bus == 0:
                        return "Any" if role == Qt.ItemDataRole.DisplayRole else 0
                    return rule.bus

        return None

    def flags(self, index: QModelIndex):
        """Return item flags including checkable col 0, editable cols 1-5, and drag support.

        Args:
            index: The model index whose flags are requested.

        Returns:
            Combined Qt.ItemFlag value for the cell.
        """
        flags = super().flags(index)
        col = index.column()
        if col == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if col in (1, 2, 3, 4, 5):
            flags |= Qt.ItemFlag.ItemIsEditable
        if index.isValid():
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def supportedDropActions(self):
        """Return MoveAction as the only supported drop action."""
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        """Return the list of MIME types supported for drag operations."""
        return [_DRAG_MIME]

    def mimeData(self, indexes):
        """Encode selected row indices into MIME data.

        Args:
            indexes: List of selected model indexes.

        Returns:
            :class:`QMimeData` with encoded row indices.
        """
        mime = QMimeData()
        rows = sorted({idx.row() for idx in indexes if idx.isValid()})
        mime.setData(_DRAG_MIME, QByteArray(b",".join(str(r).encode() for r in rows)))
        return mime

    def dropMimeData(self, data, action, row, col, parent):
        """Handle a drop event by reordering rules.

        Args:
            data: MIME data carrying the source row indices.
            action: The drop action.
            row: The destination row index (-1 means append).
            col: Ignored.
            parent: Ignored for flat table.

        Returns:
            True if the drop was handled, False otherwise.
        """
        if not data.hasFormat(_DRAG_MIME):
            return False
        src_rows = [int(r) for r in bytes(data.data(_DRAG_MIME)).split(b",")]
        dest = row if row >= 0 else self.rowCount()
        self.beginResetModel()
        offset = 0
        for src in sorted(src_rows):
            effective_src = src - offset
            effective_dest = dest - offset if dest > src else dest
            if effective_src == effective_dest:
                continue
            item = self._rules.pop(effective_src)
            self._rules.insert(effective_dest, item)
            if dest > src:
                offset += 1
        self.endResetModel()
        self.filters_changed.emit()
        return True

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """Update a rule field from a delegate or check-state change.

        For CheckStateRole on column 0 the rule's ``enabled`` flag is toggled.
        For EditRole, column values are parsed and validated according to their
        type (hex string for IDs, integer for bus, enum string for action).

        Args:
            index: The model index of the cell being edited.
            value: The new value to apply.
            role: The data role; CheckStateRole or EditRole are handled.

        Returns:
            True on success, False when the index is invalid, the role is
            unsupported, or the value fails validation.

        Emits:
            dataChanged: For the modified cell after a successful update.
            filters_changed: After any successful update.
        """
        if not index.isValid():
            return False
        rule = self._rules[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            rule.enabled = Qt.CheckState(value) == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            self.filters_changed.emit()
            return True

        if role == Qt.ItemDataRole.EditRole:
            match col:
                case 1:
                    text = str(value).strip().capitalize()
                    if text in ("Pass", "Drop"):
                        rule.action = FilterAction(text)
                    else:
                        return False
                case 2:
                    rule.name = str(value)
                case 3:
                    try:
                        rule.id_from = int(str(value), 16)
                    except ValueError:
                        return False
                case 4:
                    try:
                        rule.id_to = int(str(value), 16)
                    except ValueError:
                        return False
                case 5:
                    try:
                        rule.bus = int(value)
                    except (ValueError, TypeError):
                        return False
                case _:
                    return False

            self.dataChanged.emit(index, index)
            self.filters_changed.emit()
            return True

        return False

    # -- Public API --

    def add_rule(self, action: FilterAction = FilterAction.PASS):
        """Append a new default rule to the end of the list.

        Args:
            action: The FilterAction (Pass or Drop) for the new rule.

        Emits:
            filters_changed: After the rule has been inserted.
        """
        row = len(self._rules)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rules.append(RxFilterRule(action=action))
        self.endInsertRows()
        self.filters_changed.emit()

    def remove_rule(self, row: int):
        """Remove the rule at the given row index.

        Does nothing if ``row`` is out of bounds.

        Args:
            row: Zero-based index of the rule to remove.

        Emits:
            filters_changed: After the rule has been removed.
        """
        if 0 <= row < len(self._rules):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._rules.pop(row)
            self.endRemoveRows()
            self.filters_changed.emit()

    def move_up(self, row: int):
        """Swap the rule at ``row`` with the one above it.

        Does nothing if ``row`` is 0 or out of bounds.

        Args:
            row: Zero-based index of the rule to move up.

        Emits:
            filters_changed: After the move.
        """
        if row > 0:
            self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
            self._rules[row - 1], self._rules[row] = self._rules[row], self._rules[row - 1]
            self.endMoveRows()
            self.filters_changed.emit()

    def move_down(self, row: int):
        """Swap the rule at ``row`` with the one below it.

        Does nothing if ``row`` is already at the last position.

        Args:
            row: Zero-based index of the rule to move down.

        Emits:
            filters_changed: After the move.
        """
        if row < len(self._rules) - 1:
            # Qt requires destination > source+1 for downward move
            self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
            self._rules[row], self._rules[row + 1] = self._rules[row + 1], self._rules[row]
            self.endMoveRows()
            self.filters_changed.emit()

    @property
    def rules(self) -> list[RxFilterRule]:
        """Read-only view of the current rule list in evaluation order."""
        return self._rules

    def accepts(self, arb_id: int, bus: int) -> bool:
        """Return True if a message with the given ID and bus should be passed.

        Iterates through enabled rules in order. The first rule whose ID range
        and bus constraint match determines the outcome. If no rule matches,
        the message is passed by default.

        Args:
            arb_id: The CAN arbitration ID of the message to test.
            bus: The bus number on which the message was received.

        Returns:
            True if the message should be forwarded to consumers; False if it
            should be silently discarded.
        """
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.matches(arb_id, bus):
                return rule.action == FilterAction.PASS
        # No rule matched — pass by default
        return True

    def to_dicts(self) -> list[dict]:
        """Serialise all rules to a list of plain dicts for project persistence.

        Returns:
            A list of dicts, each with keys: enabled, action, id_from, id_to,
            bus, name.
        """
        return [
            {
                "enabled": r.enabled,
                "action": r.action.value,
                "id_from": r.id_from,
                "id_to": r.id_to,
                "bus": r.bus,
                "name": r.name,
            }
            for r in self._rules
        ]

    def clear(self):
        """Remove all filter rules and reset to empty state.

        Emits:
            filters_changed: After the model has been cleared.
        """
        if not self._rules:
            return
        self.beginResetModel()
        self._rules.clear()
        self.endResetModel()
        self.filters_changed.emit()

    def from_dicts(self, data: list[dict]):
        """Replace all rules by deserialising a list of plain dicts.

        If ``data`` is empty or results in an empty rule list, two default
        pass-all rules (Standard and Extended) are inserted automatically.

        Args:
            data: A list of dicts as produced by to_dicts().

        Emits:
            filters_changed: After the model has been fully rebuilt.
        """
        self.beginResetModel()
        self._rules.clear()
        for d in data:
            self._rules.append(RxFilterRule(
                enabled=d.get("enabled", True),
                action=FilterAction(d.get("action", "Pass")),
                id_from=d.get("id_from", 0),
                id_to=d.get("id_to", 0x7FF),
                bus=d.get("bus", 0),
                name=d.get("name", ""),
            ))
        if not self._rules:
            self._rules.append(RxFilterRule(name="Standard", action=FilterAction.PASS,
                                            id_from=0x000, id_to=0x7FF))
            self._rules.append(RxFilterRule(name="Extended", action=FilterAction.PASS,
                                            id_from=0x800, id_to=0x1FFFFFFF))
        self.endResetModel()
        self.filters_changed.emit()
