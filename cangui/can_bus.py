"""CAN bus abstraction layer wrapping python-can.

This module has no Qt dependency and can be used safely in the Core layer.
The :class:`BusConfig` dataclass carries all connection parameters;
:class:`CanBus` wraps a ``python-can`` ``Bus`` object with a cangui-friendly
API that returns :class:`~cangui.can_message.CanMessage` instances.
"""

import sys
from dataclasses import dataclass

import can

from cangui.can_message import CanMessage

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class BusConfig:
    """Configuration for a single CAN connection.

    Attributes:
        interface: python-can interface name as stored in the project file
            (e.g. ``"socketcan"``, ``"socketcan-virtual"``, ``"pcan"``,
            ``"virtual"``).  The value ``"socketcan-virtual"`` is remapped to
            ``"socketcan"`` at connection time via :attr:`can_interface`.
        channel: Interface-specific channel string (e.g. ``"vcan0"`` for
            SocketCAN, ``"PCAN_USBBUS1"`` for PEAK).  Empty string for the
            python-can ``virtual`` interface.
        bitrate: Nominal bit-rate in bits per second.  Ignored for virtual
            interfaces.
        fd: ``True`` to open the bus in CAN FD mode.
        name: Optional human-readable label displayed in the connection table.
        bus_number: Logical bus number (1-based).  Used to route TX frames
            from :class:`~cangui.model_tx_message.TxMessageModel` to the
            correct physical bus.
    """

    interface: str = "virtual" if _IS_WINDOWS else "socketcan-virtual"
    channel: str = "" if _IS_WINDOWS else "vcan0"
    bitrate: int = 500000
    fd: bool = False
    listen_only: bool = False
    name: str = ""
    bus_number: int = 1

    @property
    def is_virtual(self) -> bool:
        """``True`` if this is a loopback/virtual bus.

        Virtual buses receive their own transmitted frames, which means the
        RX path should not filter out outgoing messages.
        """
        return self.interface in ("socketcan-virtual", "virtual")

    @property
    def can_interface(self) -> str:
        """The python-can interface string passed to ``can.Bus()``.

        Translates the cangui-internal ``"socketcan-virtual"`` alias to
        the standard ``"socketcan"`` interface name.
        """
        if self.interface == "socketcan-virtual":
            return "socketcan"
        return self.interface


class CanBus:
    """Thin wrapper around a python-can ``Bus`` object.

    Translates between python-can's ``can.Message`` type and cangui's
    :class:`~cangui.can_message.CanMessage`.  The wrapper is intentionally
    minimal: all higher-level concerns (batching, threading, filtering) are
    handled by the Service and Worker layers.

    Args:
        config: Connection parameters for this bus instance.
    """

    def __init__(self, config: BusConfig):
        """Store *config*; does not open the underlying python-can bus."""
        self.config = config
        self._bus: can.Bus | None = None

    @property
    def is_connected(self) -> bool:
        """``True`` while an active python-can ``Bus`` object is open."""
        return self._bus is not None

    @property
    def bus_state(self) -> "can.BusState | None":
        """Return the hardware error state of the bus.

        Queries the python-can ``Bus.state`` property which is supported by
        most hardware drivers (PCAN, SocketCAN, Vector).  Virtual and SLCAN
        interfaces may not implement it; in that case ``None`` is returned so
        that callers can skip state-based status updates.

        Returns:
            ``can.BusState.ACTIVE``, ``can.BusState.PASSIVE``, or
            ``can.BusState.ERROR`` if the interface supports bus-state polling;
            ``None`` if disconnected or if the interface does not support it.
        """
        if self._bus is None:
            return None
        try:
            return self._bus.state
        except Exception:
            return None

    def connect(self):
        """Open the CAN bus using the parameters in :attr:`config`.

        Raises:
            can.CanError: If the interface cannot be opened (wrong channel,
                missing driver, etc.).
            Exception: Any exception raised by python-can is propagated; the
                caller (:class:`~cangui.service_can.CanService`) catches it
                and records the error in the connection status.
        """
        self._bus = can.Bus(
            interface=self.config.can_interface,
            channel=self.config.channel,
            bitrate=self.config.bitrate,
            fd=self.config.fd,
            listen_only=self.config.listen_only,
            receive_own_messages=self.config.is_virtual,
        )

    def disconnect(self):
        """Shut down and release the underlying python-can ``Bus``.

        Safe to call when already disconnected (no-op).
        """
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def recv(self, timeout: float = 0.1) -> CanMessage | None:
        """Receive one CAN frame, blocking up to *timeout* seconds.

        Called from :class:`~cangui.worker_can_receiver.CanReceiver` which
        runs in a background ``QThread``.  The timeout keeps the worker
        responsive to stop requests even during bus silence.

        Args:
            timeout: Maximum wait time in seconds.  Defaults to 100 ms.

        Returns:
            A :class:`~cangui.can_message.CanMessage` if a frame arrived
            within the timeout, or ``None`` on timeout or if disconnected.

        Note:
            On virtual buses, all frames are treated as received (``is_rx``
            is always ``True``) because ``receive_own_messages=True`` is set
            at connection time and the hardware does not set the RX flag on
            echoed frames.
        """
        if self._bus is None:
            return None
        msg = self._bus.recv(timeout=timeout)
        if msg is None:
            return None
        # On virtual buses, treat own echoed frames as received
        is_rx = True if self.config.is_virtual else msg.is_rx
        return CanMessage(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            is_extended_id=msg.is_extended_id,
            is_fd=msg.is_fd,
            is_remote_frame=msg.is_remote_frame,
            is_error_frame=msg.is_error_frame,
            is_rx=is_rx,
            dlc=msg.dlc,
            timestamp=msg.timestamp,
            bus=self.config.bus_number,
            channel=self.config.channel,
        )

    def send(self, msg: CanMessage):
        """Transmit a CAN frame on the bus.

        Silently does nothing when the bus is not connected.  DLC trimming
        ensures that ``data`` is never longer than ``dlc`` bytes, which is
        required by some hardware drivers.

        Args:
            msg: Frame to transmit.  ``msg.dlc`` controls how many bytes of
                ``msg.data`` are actually sent.

        Raises:
            can.CanError: Propagated from python-can on hardware errors.
        """
        if self._bus is None:
            return
        data = msg.data[:msg.dlc] if msg.dlc else msg.data
        can_msg = can.Message(
            arbitration_id=msg.arbitration_id,
            data=data,
            is_extended_id=msg.is_extended_id,
            is_fd=msg.is_fd,
            is_remote_frame=msg.is_remote_frame,
        )
        self._bus.send(can_msg)
