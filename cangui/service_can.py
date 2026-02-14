from PySide6.QtCore import QObject, Signal

from cangui.can_bus import CanBus, BusConfig
from cangui.can_message import CanMessage
from cangui.worker_can_receiver import CanReceiver
from cangui.service_message_dispatcher import MessageDispatcher


class ConnectionInfo:
    def __init__(self, config: BusConfig):
        self.config = config
        self.bus = CanBus(config)
        self.receiver: CanReceiver | None = None
        self.status: str = "Disconnected"
        self.overruns: int = 0
        self.qxmt_fulls: int = 0

    @property
    def name(self) -> str:
        return self.config.name or f"Connection{self.config.bus_number}"



class CanService(QObject):
    connection_added = Signal(int)  # connection index
    connection_removed = Signal(int)
    connection_status_changed = Signal(int, str)  # index, status

    def __init__(self, dispatcher: MessageDispatcher, parent=None):
        super().__init__(parent)
        self._dispatcher = dispatcher
        self._connections: list[ConnectionInfo] = []

    @property
    def connections(self) -> list[ConnectionInfo]:
        return self._connections

    def add_connection(self, config: BusConfig) -> int:
        conn = ConnectionInfo(config)
        index = len(self._connections)
        self._connections.append(conn)
        self.connection_added.emit(index)
        return index

    def remove_connection(self, index: int):
        if 0 <= index < len(self._connections):
            self.disconnect(index)
            self._connections.pop(index)
            self.connection_removed.emit(index)

    def connect(self, index: int):
        if not (0 <= index < len(self._connections)):
            return
        conn = self._connections[index]
        try:
            conn.bus.connect()
            conn.receiver = CanReceiver(conn.bus)
            conn.receiver.message_received.connect(self._dispatcher.dispatch_batch)
            conn.receiver.start()
            conn.status = "OK"
        except Exception as e:
            conn.status = f"Error: {e}"
        self.connection_status_changed.emit(index, conn.status)

    def disconnect(self, index: int):
        if not (0 <= index < len(self._connections)):
            return
        conn = self._connections[index]
        if conn.receiver is not None:
            conn.receiver.stop()
            conn.receiver = None
        conn.bus.disconnect()
        conn.status = "Disconnected"
        self.connection_status_changed.emit(index, conn.status)

    def connect_all(self):
        for i in range(len(self._connections)):
            if not self._connections[i].bus.is_connected:
                self.connect(i)

    def disconnect_all(self):
        for i in range(len(self._connections)):
            if self._connections[i].bus.is_connected:
                self.disconnect(i)

    def reset(self):
        for i in range(len(self._connections)):
            self.disconnect(i)
            self.connect(i)
