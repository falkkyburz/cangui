from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QStyledItemDelegate, QComboBox

from cangui.can_bus import BusConfig
from cangui.service_can import CanService

INTERFACES = [
    "socketcan-virtual", "socketcan", "pcan", "ixxat", "kvaser", "vector", "virtual",
]

DEFAULT_CHANNELS = {
    "socketcan-virtual": "vcan0",
    "socketcan": "can0",
    "pcan": "PCAN_USBBUS1",
    "vector": "0",
}

BITRATES = [125000, 250000, 500000, 1000000]


class InterfaceDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Interface column."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(INTERFACES)
        return combo

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class ConnectionModel(QAbstractTableModel):
    COLUMNS = ["", "Bus", "Name", "Channel", "Interface", "Bit Rate", "Status",
               "Overruns", "QXmtFulls", "Options", "Bus Load"]

    def __init__(self, can_service: CanService, parent=None):
        super().__init__(parent)
        self._service = can_service
        self._service.connection_added.connect(self._on_connection_added)
        self._service.connection_removed.connect(self._on_connection_removed)
        self._service.connection_status_changed.connect(self._on_status_changed)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._service.connections)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        conn = self._service.connections[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if conn.bus.is_connected else Qt.CheckState.Unchecked

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            match col:
                case 1:
                    return conn.config.bus_number
                case 2:
                    return conn.name
                case 3:
                    return conn.config.channel
                case 4:
                    return conn.config.interface
                case 5:
                    if role == Qt.ItemDataRole.EditRole:
                        return conn.config.bitrate
                    return f"{conn.config.bitrate // 1000} kbit/s"
                case 6:
                    return conn.status
                case 7:
                    return conn.overruns
                case 8:
                    return conn.qxmt_fulls
                case 9:
                    return "EF" if not conn.config.fd else "FD"
                case 10:
                    return ""

        if role == Qt.ItemDataRole.ForegroundRole and col == 6:
            from PySide6.QtGui import QColor
            if conn.status == "OK":
                return QColor("green")
            elif conn.status.startswith("Error"):
                return QColor("red")

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
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            row = index.row()
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self._service.connect(row)
            else:
                self._service.disconnect(row)
            return True

        if role == Qt.ItemDataRole.EditRole:
            row = index.row()
            conn = self._service.connections[row]
            was_connected = conn.bus.is_connected
            col = index.column()

            match col:
                case 1:
                    try:
                        conn.config.bus_number = int(value)
                    except (ValueError, TypeError):
                        return False
                case 2:
                    conn.config.name = str(value)
                case 3:
                    conn.config.channel = str(value)
                case 4:
                    new_iface = str(value)
                    conn.config.interface = new_iface
                    default_ch = DEFAULT_CHANNELS.get(new_iface)
                    if default_ch:
                        conn.config.channel = default_ch
                        ch_idx = self.index(row, 3)
                        self.dataChanged.emit(ch_idx, ch_idx)
                case 5:
                    try:
                        conn.config.bitrate = int(value)
                    except (ValueError, TypeError):
                        return False
                case _:
                    return False

            # Reconnect if was active
            if was_connected and col in (3, 4, 5):
                self._service.disconnect(row)
                self._service.connect(row)

            self.dataChanged.emit(index, index)
            return True

        return False

    def add_empty_row(self):
        next_bus = len(self._service.connections) + 1
        config = BusConfig(
            interface="socketcan-virtual",
            channel="vcan0",
            bitrate=500000,
            bus_number=next_bus,
        )
        self._service.add_connection(config)

    def remove_row(self, row: int):
        self._service.remove_connection(row)

    def _on_connection_added(self, index: int):
        self.beginInsertRows(QModelIndex(), index, index)
        self.endInsertRows()

    def _on_connection_removed(self, index: int):
        self.beginRemoveRows(QModelIndex(), index, index)
        self.endRemoveRows()

    def _on_status_changed(self, index: int, _status: str):
        left = self.index(index, 0)
        right = self.index(index, self.columnCount() - 1)
        self.dataChanged.emit(left, right)
