from dataclasses import dataclass, field
import time


@dataclass
class CanMessage:
    arbitration_id: int
    data: bytes
    is_extended_id: bool = False
    is_fd: bool = False
    is_remote_frame: bool = False
    is_error_frame: bool = False
    is_rx: bool = True
    dlc: int = 0
    timestamp: float = field(default_factory=time.time)
    bus: int = 1
    channel: str = ""

    @property
    def id_hex(self) -> str:
        if self.is_extended_id:
            return f"{self.arbitration_id:08X}"
        return f"{self.arbitration_id:03X}"

    @property
    def frame_type(self) -> str:
        if self.is_error_frame:
            return "Error"
        if self.is_remote_frame:
            return "RTR"
        if self.is_fd:
            return "FD"
        return "Data"

    @property
    def data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)
