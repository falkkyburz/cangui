from dataclasses import dataclass

import can

from cangui.core.can_message import CanMessage


@dataclass
class BusConfig:
    interface: str = "socketcan-virtual"
    channel: str = "vcan0"
    bitrate: int = 500000
    fd: bool = False
    name: str = ""
    bus_number: int = 1

    @property
    def is_virtual(self) -> bool:
        return self.interface == "socketcan-virtual"

    @property
    def can_interface(self) -> str:
        """The python-can interface name."""
        if self.interface == "socketcan-virtual":
            return "socketcan"
        return self.interface


class CanBus:
    def __init__(self, config: BusConfig):
        self.config = config
        self._bus: can.Bus | None = None

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    def connect(self):
        self._bus = can.Bus(
            interface=self.config.can_interface,
            channel=self.config.channel,
            bitrate=self.config.bitrate,
            fd=self.config.fd,
            receive_own_messages=True,
        )

    def disconnect(self):
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def recv(self, timeout: float = 0.1) -> CanMessage | None:
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
