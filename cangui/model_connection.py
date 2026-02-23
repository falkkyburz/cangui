"""Qt table model for managing CAN bus connections.

Wraps CanService's connection list and exposes it as a QAbstractTableModel so
that a QTableView can display and edit connection parameters (interface, channel,
bitrate, name) in-place. Toggling the check-box in column 0 connects or
disconnects the bus via CanService.

Platform notes:
    On Windows, Linux-only SocketCAN interfaces are hidden from the interface
    drop-down.
"""

import sys

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QStyledItemDelegate, QComboBox

from cangui.can_bus import BusConfig
from cangui.service_can import CanService

_LINUX_INTERFACES = ["socketcan-virtual", "socketcan"]
_ALL_INTERFACES = _LINUX_INTERFACES + ["pcan", "vector", "slcan", "virtual"]

# Hide Linux-only SocketCAN interfaces on Windows
INTERFACES = (
    [i for i in _ALL_INTERFACES if i not in _LINUX_INTERFACES]
    if sys.platform == "win32"
    else _ALL_INTERFACES
)

DEFAULT_CHANNELS = {
    "socketcan-virtual": "vcan0",
    "socketcan": "can0",
    "pcan": "PCAN_USBBUS1",
    "vector": "0",
    "slcan": "COM1" if sys.platform == "win32" else "/dev/ttyUSB0",
    "virtual": "",
}

BITRATES = [125000, 250000, 500000, 1000000]


class InterfaceDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Interface column.

    Replaces the default line-edit with a QComboBox pre-populated with the
    platform-appropriate set of python-can interface names.
    """

    def createEditor(self, parent, option, index):
        """Create a QComboBox populated with available interface names.

        Args:
            parent: The parent widget for the editor.
            option: Style options (unused).
            index: The model index being edited (unused).

        Returns:
            A QComboBox containing all platform-appropriate interface strings.
        """
        combo = QComboBox(parent)
        combo.addItems(INTERFACES)
        return combo

    def setEditorData(self, editor, index):
        """Pre-select the current interface value in the combo box.

        Args:
            editor: The QComboBox created by createEditor().
            index: The model index providing the current value via EditRole.
        """
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Write the user's combo-box selection back to the model.

        Args:
            editor: The QComboBox whose current text is the new value.
            model: The model to update.
            index: The model index to update.
        """
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class ConnectionModel(QAbstractTableModel):
    """Table model that exposes the CanService connection list for display and editing.

    Each row corresponds to one CAN connection held by CanService. The model
    listens to CanService signals (connection_added, connection_removed,
    connection_status_changed) and forwards them to the Qt model notification
    system so that attached views refresh automatically.

    Column layout (COLUMNS):
        0: connect/disconnect check-box (CheckStateRole).
        1: bus number (read-only).
        2: connection name (editable).
        3: channel string (editable).
        4: interface name (editable via InterfaceDelegate).
        5: bit rate in bps (editable; displayed as "N kbit/s").
        6: status string (colour-coded: green = OK, amber = Bus Heavy, red = Bus Off / Error*).
        7: overrun counter (read-only).
        8: Tx-queue-full counter (read-only).
        9: frame format ("FD" or "EF", read-only).
        10: bus load percentage (read-only, currently empty).
    """

    COLUMNS = ["", "Bus", "Name", "Channel", "Interface", "Bit Rate", "Status",
               "Overruns", "QXmtFulls", "Listen Only", "Bus Load"]

    def __init__(self, can_service: CanService, parent=None):
        """Initialise the model and connect to CanService signals.

        Args:
            can_service: The application-level CAN service whose connection
                list this model represents.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._service = can_service
        self._service.connection_added.connect(self._on_connection_added)
        self._service.connection_removed.connect(self._on_connection_removed)
        self._service.connection_status_changed.connect(self._on_status_changed)
        self._service.bus_load_updated.connect(self._on_load_updated)

    def rowCount(self, parent=QModelIndex()) -> int:
        """Return the number of CAN connections managed by CanService.

        Args:
            parent: Must be an invalid index for a flat table model.

        Returns:
            Number of connections, or 0 when parent is valid.
        """
        if parent.isValid():
            return 0
        return len(self._service.connections)

    def columnCount(self, parent=QModelIndex()) -> int:
        """Return the fixed number of columns.

        Args:
            parent: Unused for a flat table model.

        Returns:
            The number of entries in COLUMNS.
        """
        return len(self.COLUMNS)

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
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display, edit, foreground-colour, or check-state data for a cell.

        Args:
            index: The model index identifying the cell.
            role: The data role being queried.

        Returns:
            The requested cell value, or None for unsupported roles or invalid
            indices. The Status column (6) returns a QColor for ForegroundRole.
        """
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
                case 10:
                    if conn.bus.is_connected and conn.bus_load > 0.0:
                        return f"{conn.bus_load:.0f}%"
                    return ""

        if role == Qt.ItemDataRole.CheckStateRole and col == 9:
            return Qt.CheckState.Checked if conn.config.listen_only else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.ForegroundRole and col == 6:
            from PySide6.QtGui import QColor
            if conn.status == "OK":
                return QColor("#2E7D32")   # green
            elif conn.status == "Bus Heavy":
                return QColor("#E67E00")   # amber — error-passive, still operational
            elif conn.status in ("Bus Off",) or conn.status.startswith("Error"):
                return QColor("#C62828")   # red

        if role == Qt.ItemDataRole.DecorationRole and col == 6:
            from cangui.icons import icon as _icon
            if conn.status == "OK":
                return _icon("status-ok")
            elif conn.status == "Bus Heavy":
                return _icon("status-warn")
            elif conn.status in ("Bus Off",) or conn.status.startswith("Error"):
                return _icon("status-error")
            else:
                return _icon("status-off")

        return None

    def flags(self, index: QModelIndex):
        """Return item flags, making column 0 checkable and columns 2–5 editable.

        Args:
            index: The model index whose flags are requested.

        Returns:
            Combined Qt.ItemFlag value for the cell.
        """
        flags = super().flags(index)
        col = index.column()
        if col in (0, 9):
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if col in (2, 3, 4, 5):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """Apply a value change from the view to the underlying connection config.

        For CheckStateRole on column 0, the connection is connected or
        disconnected via CanService. For EditRole on the editable columns, the
        connection config is updated; if the connection was active and a
        transport-affecting field (channel, interface, bitrate) changed, it is
        automatically reconnected.

        When the interface changes (column 4), the channel field (column 3) is
        also updated to the platform default for that interface and a separate
        dataChanged is emitted for it.

        Args:
            index: The model index of the cell being edited.
            value: The new value to apply.
            role: The data role; CheckStateRole or EditRole are handled.

        Returns:
            True on success, False when the role is unsupported or the value
            fails type conversion.

        Emits:
            dataChanged: For the modified cell (and possibly column 3 when the
                interface changes).
        """
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            row = index.row()
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self._service.connect(row)
            else:
                self._service.disconnect(row)
            return True

        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 9:
            row = index.row()
            conn = self._service.connections[row]
            conn.config.listen_only = Qt.CheckState(value) == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            return True

        if role == Qt.ItemDataRole.EditRole:
            row = index.row()
            conn = self._service.connections[row]
            was_connected = conn.bus.is_connected
            col = index.column()

            match col:
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
        """Add a new connection with platform-appropriate defaults to CanService.

        On Linux a socketcan-virtual/vcan0 connection is created; on Windows a
        virtual connection with an empty channel is used. The new connection is
        assigned the next available bus number. CanService emits
        ``connection_added`` which causes this model to insert the corresponding
        row automatically.
        """
        next_bus = len(self._service.connections) + 1
        if sys.platform == "win32":
            iface, channel = "virtual", ""
        else:
            iface, channel = "socketcan-virtual", "vcan0"
        config = BusConfig(
            interface=iface,
            channel=channel,
            bitrate=500000,
            bus_number=next_bus,
        )
        self._service.add_connection(config)

    def remove_row(self, row: int):
        """Remove the connection at the given row via CanService.

        CanService emits ``connection_removed`` which triggers the corresponding
        model row removal automatically.

        Args:
            row: Zero-based index of the connection to remove.
        """
        self._service.remove_connection(row)

    def _on_connection_added(self, index: int):
        """Handle CanService.connection_added by inserting the new model row.

        Args:
            index: The zero-based position at which the connection was inserted.
        """
        self.beginInsertRows(QModelIndex(), index, index)
        self.endInsertRows()

    def _on_connection_removed(self, index: int):
        """Handle CanService.connection_removed by removing the model row.

        Args:
            index: The zero-based position of the connection that was removed.
        """
        self.beginRemoveRows(QModelIndex(), index, index)
        self.endRemoveRows()

    def _on_status_changed(self, index: int, _status: str):
        """Handle CanService.connection_status_changed by refreshing the whole row.

        Status changes may affect multiple columns (e.g. colour-coded status,
        overrun counters), so the entire row range is invalidated.

        Args:
            index: The zero-based row whose connection status changed.
            _status: The new status string (unused; the model re-reads from the
                connection object on the next data() call).

        Emits:
            dataChanged: Spanning all columns of the affected row.
        """
        left = self.index(index, 0)
        right = self.index(index, self.columnCount() - 1)
        self.dataChanged.emit(left, right)

    def _on_load_updated(self, index: int, _load: float):
        """Handle CanService.bus_load_updated by refreshing the Bus Load column.

        Args:
            index: The zero-based row whose bus load changed.
            _load: The new load percentage (unused; the model re-reads from
                the connection object on the next data() call).

        Emits:
            dataChanged: For the Bus Load cell of the affected row.
        """
        col = self.COLUMNS.index("Bus Load")
        cell = self.index(index, col)
        self.dataChanged.emit(cell, cell)
