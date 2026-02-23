"""CAN connection lifecycle service.

Manages a list of :class:`ConnectionInfo` objects, each pairing a
:class:`~cangui.can_bus.BusConfig` with an open :class:`~cangui.can_bus.CanBus`
and an optional :class:`~cangui.worker_can_receiver.CanReceiver` thread.
"""

import can
from PySide6.QtCore import QObject, Signal, QTimer

from cangui.can_bus import CanBus, BusConfig
from cangui.can_message import CanMessage
from cangui.worker_can_receiver import CanReceiver
from cangui.service_message_dispatcher import MessageDispatcher


class ConnectionInfo:
    """Runtime state for one CAN connection.

    Created by :class:`CanService` when a connection is added.

    Attributes:
        config: Immutable configuration for this connection.
        bus: :class:`~cangui.can_bus.CanBus` wrapping the python-can bus.
        receiver: Active :class:`~cangui.worker_can_receiver.CanReceiver`
            thread, or ``None`` when disconnected.
        status: Human-readable status string: ``"OK"``, ``"Disconnected"``,
            or ``"Error: <message>"``.
        overruns: Error counter (unused, reserved for future hardware stats).
        qxmt_fulls: Error counter (unused, reserved for future hardware stats).
    """

    def __init__(self, config: BusConfig):
        """Initialise with a bus configuration; does not open the bus."""
        self.config = config
        self.bus = CanBus(config)
        self.receiver: CanReceiver | None = None
        self.status: str = "Disconnected"
        self.overruns: int = 0
        self.qxmt_fulls: int = 0
        self.bus_load: float = 0.0
        self._bits_in_window: int = 0

    @property
    def name(self) -> str:
        """Display name: :attr:`BusConfig.name` or ``"Connection<bus_number>"``."""
        return self.config.name or f"Connection{self.config.bus_number}"


class CanService(QObject):
    """Owns all active CAN connections and their receiver threads.

    Provides add/remove/connect/disconnect lifecycle operations.  When a bus
    transitions to ``"OK"``, :attr:`connection_status_changed` fires so that
    :class:`~cangui.ui_main_window.MainWindow` can start the TX transmitter.

    Signals:
        connection_added: Emitted with the new connection's index after it is
            appended to the internal list.
        connection_removed: Emitted with the removed connection's former index.
        connection_status_changed: Emitted with ``(index, status_string)``
            after every connect/disconnect/error event.

    Args:
        dispatcher: The :class:`~cangui.service_message_dispatcher.MessageDispatcher`
            that receives batches from each :class:`CanReceiver`.
        parent: Optional Qt parent object.
    """

    connection_added = Signal(int)       # connection index
    connection_removed = Signal(int)
    connection_status_changed = Signal(int, str)  # index, status string
    bus_load_updated = Signal(int, float)  # index, load percentage

    def __init__(self, dispatcher: MessageDispatcher, parent=None):
        """Initialise the service with a message dispatcher; no connections are open yet."""
        super().__init__(parent)
        self._dispatcher = dispatcher
        self._connections: list[ConnectionInfo] = []
        self._state_poll_timer = QTimer(self)
        self._state_poll_timer.setInterval(500)
        self._state_poll_timer.timeout.connect(self._poll_bus_states)
        self._state_poll_timer.start()

    @property
    def connections(self) -> list[ConnectionInfo]:
        """Read-only view of the current connection list."""
        return self._connections

    def add_connection(self, config: BusConfig) -> int:
        """Append a new (disconnected) connection.

        Args:
            config: Connection parameters.

        Returns:
            Index of the newly added connection.

        Emits:
            connection_added: With the new index.
        """
        conn = ConnectionInfo(config)
        index = len(self._connections)
        self._connections.append(conn)
        self.connection_added.emit(index)
        return index

    def remove_connection(self, index: int):
        """Disconnect and remove the connection at *index*.

        Args:
            index: Zero-based position in the connections list.

        Emits:
            connection_removed: With the removed index.
        """
        if 0 <= index < len(self._connections):
            self.disconnect(index)
            self._connections.pop(index)
            self.connection_removed.emit(index)

    def connect(self, index: int):
        """Open the bus and start the receiver thread for connection *index*.

        On success sets ``status = "OK"`` and starts the
        :class:`~cangui.worker_can_receiver.CanReceiver`.  On failure sets
        ``status = "Error: <exception>"`` and leaves the bus closed.

        Args:
            index: Zero-based position in the connections list.

        Emits:
            connection_status_changed: With the new status string.
        """
        if not (0 <= index < len(self._connections)):
            return
        conn = self._connections[index]
        try:
            conn.bus.connect()
            conn.receiver = CanReceiver(conn.bus)
            conn.receiver.message_received.connect(self._dispatcher.dispatch_batch)
            conn.receiver.message_received.connect(
                lambda batch, c=conn: self._accum_bits(c, batch)
            )
            conn.receiver.start()
            conn.status = "OK"
        except Exception as e:
            conn.status = f"Error: {e}"
        self.connection_status_changed.emit(index, conn.status)

    def disconnect(self, index: int):
        """Stop the receiver thread and close the bus for connection *index*.

        Safe to call when already disconnected.

        Args:
            index: Zero-based position in the connections list.

        Emits:
            connection_status_changed: With ``"Disconnected"``.
        """
        if not (0 <= index < len(self._connections)):
            return
        conn = self._connections[index]
        if conn.receiver is not None:
            conn.receiver.stop()
            conn.receiver = None
        conn.bus.disconnect()
        conn.status = "Disconnected"
        conn._bits_in_window = 0
        conn.bus_load = 0.0
        self.connection_status_changed.emit(index, conn.status)

    def connect_all(self):
        """Connect all currently disconnected connections."""
        for i in range(len(self._connections)):
            if not self._connections[i].bus.is_connected:
                self.connect(i)

    def disconnect_all(self):
        """Disconnect all currently connected connections."""
        for i in range(len(self._connections)):
            if self._connections[i].bus.is_connected:
                self.disconnect(i)

    def reset(self):
        """Disconnect then reconnect every connection that was active.

        Used to flush hardware buffers and restart receiver threads after a
        configuration change.
        """
        for i in range(len(self._connections)):
            was_connected = self._connections[i].bus.is_connected
            self.disconnect(i)
            if was_connected:
                self.connect(i)

    def _accum_bits(self, conn: ConnectionInfo, batch: list) -> None:
        """Accumulate estimated bit count from a received message batch.

        Called on the main thread via a queued connection from
        :class:`~cangui.worker_can_receiver.CanReceiver`.  Uses a conservative
        frame overhead estimate (47 bits for standard, 67 bits for extended
        ID) plus 8 bits per data byte, without bit-stuffing correction.

        Args:
            conn: The connection whose bit counter to update.
            batch: List of :class:`~cangui.can_message.CanMessage` objects.
        """
        for msg in batch:
            overhead = 67 if msg.is_extended_id else 47
            conn._bits_in_window += overhead + 8 * msg.dlc

    def _poll_bus_states(self):
        """Poll each active bus for hardware error state and update status.

        Called every 500 ms by an internal QTimer on the main thread.  Queries
        :attr:`~cangui.can_bus.CanBus.bus_state` for every connected bus and
        maps the python-can ``BusState`` enum to a human-readable status
        string:

        - ``ACTIVE`` → ``"OK"``
        - ``PASSIVE`` → ``"Bus Heavy"`` (error-passive; TX/RX still functional
          but error counters exceeded 127)
        - ``ERROR`` → ``"Bus Off"`` (error counter exceeded 255; bus halted)

        If an interface does not support bus-state polling (virtual, SLCAN)
        ``bus_state`` returns ``None`` and no update is made.  Status strings
        are only emitted when they change to avoid unnecessary view refreshes.

        Also computes bus load from bits accumulated over the 500 ms window and
        emits :attr:`bus_load_updated` when the value changes by ≥ 0.5 %.

        Emits:
            connection_status_changed: When the polled state differs from the
                current ``ConnectionInfo.status``.
            bus_load_updated: When the computed load differs from the stored
                value by at least 0.5 percentage points.
        """
        for i, conn in enumerate(self._connections):
            if conn.bus.is_connected:
                state = conn.bus.bus_state
                if state is not None:
                    if state == can.BusState.ERROR:
                        new_status = "Bus Off"
                    elif state == can.BusState.PASSIVE:
                        new_status = "Bus Heavy"
                    else:
                        new_status = "OK"
                    if conn.status != new_status:
                        conn.status = new_status
                        self.connection_status_changed.emit(i, new_status)

                if conn.config.bitrate > 0:
                    load = min(
                        (conn._bits_in_window / (conn.config.bitrate * 0.5)) * 100.0,
                        100.0,
                    )
                    conn._bits_in_window = 0
                    if abs(load - conn.bus_load) >= 0.5:
                        conn.bus_load = load
                        self.bus_load_updated.emit(i, load)
            elif conn.bus_load > 0.0:
                conn.bus_load = 0.0
                conn._bits_in_window = 0
                self.bus_load_updated.emit(i, 0.0)
