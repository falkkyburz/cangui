from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal


class FilterAction(Enum):
    PASS = "Pass"
    DROP = "Drop"


@dataclass
class RxFilterRule:
    enabled: bool = True
    action: FilterAction = FilterAction.PASS
    id_from: int = 0x000
    id_to: int = 0x7FF
    bus: int = 0  # 0 = any bus
    name: str = ""

    def matches(self, arb_id: int, bus: int) -> bool:
        if self.bus != 0 and self.bus != bus:
            return False
        return self.id_from <= arb_id <= self.id_to


COLUMNS = ["", "Action", "Name", "CAN-ID From", "CAN-ID To", "Bus"]


class RxFilterModel(QAbstractTableModel):
    """First-match filter table for incoming CAN messages.

    Rules are evaluated top-to-bottom. The first enabled rule whose
    CAN-ID range and bus match determines whether the message is
    passed or dropped.  If no rule matches the message is passed.
    """

    filters_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules: list[RxFilterRule] = [
            RxFilterRule(name="Standard", action=FilterAction.PASS,
                         id_from=0x000, id_to=0x7FF),
            RxFilterRule(name="Extended", action=FilterAction.PASS,
                         id_from=0x800, id_to=0x1FFFFFFF),
        ]

    # -- Qt model interface --

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rules)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
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
        flags = super().flags(index)
        col = index.column()
        if col == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if col in (1, 2, 3, 4, 5):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
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
        row = len(self._rules)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rules.append(RxFilterRule(action=action))
        self.endInsertRows()
        self.filters_changed.emit()

    def remove_rule(self, row: int):
        if 0 <= row < len(self._rules):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._rules.pop(row)
            self.endRemoveRows()
            self.filters_changed.emit()

    def move_up(self, row: int):
        if row > 0:
            self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row - 1)
            self._rules[row - 1], self._rules[row] = self._rules[row], self._rules[row - 1]
            self.endMoveRows()
            self.filters_changed.emit()

    def move_down(self, row: int):
        if row < len(self._rules) - 1:
            # Qt requires destination > source+1 for downward move
            self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), row + 2)
            self._rules[row], self._rules[row + 1] = self._rules[row + 1], self._rules[row]
            self.endMoveRows()
            self.filters_changed.emit()

    @property
    def rules(self) -> list[RxFilterRule]:
        return self._rules

    def accepts(self, arb_id: int, bus: int) -> bool:
        """Return True if the message should be passed through."""
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.matches(arb_id, bus):
                return rule.action == FilterAction.PASS
        # No rule matched — pass by default
        return True

    def to_dicts(self) -> list[dict]:
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

    def from_dicts(self, data: list[dict]):
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
