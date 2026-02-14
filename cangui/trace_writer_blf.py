import can
from pathlib import Path

from cangui.can_message import CanMessage
from cangui.trace_writer import TraceWriter


class BlfTraceWriter(TraceWriter):
    """Writes CAN messages to Vector BLF binary format using python-can."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._writer: can.BLFWriter | None = None
        self._msg_number = 0
        self._start_time: float | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def message_count(self) -> int:
        return self._msg_number

    def open(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = can.BLFWriter(self._path)
        self._msg_number = 0
        self._start_time = None

    @property
    def file_size(self) -> int:
        if self._writer is None:
            return 0
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    @property
    def is_open(self) -> bool:
        return self._writer is not None

    def write(self, msg: CanMessage, direction: str = "Rx"):
        if self._writer is None:
            return
        if self._start_time is None:
            self._start_time = msg.timestamp
        self._msg_number += 1
        can_msg = can.Message(
            arbitration_id=msg.arbitration_id,
            data=msg.data,
            is_extended_id=msg.is_extended_id,
            is_fd=msg.is_fd,
            is_remote_frame=msg.is_remote_frame,
            is_error_frame=msg.is_error_frame,
            dlc=msg.dlc or len(msg.data),
            timestamp=msg.timestamp,
            channel=msg.channel or str(msg.bus),
        )
        self._writer.on_message_received(can_msg)

    def close(self):
        if self._writer is not None:
            self._writer.stop()
            self._writer = None
