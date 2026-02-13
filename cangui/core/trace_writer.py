from datetime import datetime
from pathlib import Path

from cangui.core.can_message import CanMessage


class TraceWriter:
    """Writes CAN messages to PEAK-compatible ASCII TRC files."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file = None
        self._start_time: float | None = None
        self._msg_number = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def message_count(self) -> int:
        return self._msg_number

    def open(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w")
        self._start_time = None
        self._msg_number = 0
        now = datetime.now()
        self._file.write(";$FILEVERSION=1.1\n")
        self._file.write(f";   Start time: {now:%m/%d/%Y %H:%M:%S.%f}\n")
        self._file.write(
            ";-----------------------------------------------"
            "--------------------------------\n"
        )
        self._file.write(
            ";   Message Number) Time Offset   Type   ID"
            "    Rx/Tx   d]  Data Bytes ...\n"
        )
        self._file.write(
            ";-----------------------------------------------"
            "--------------------------------\n"
        )

    @property
    def file_size(self) -> int:
        """Return current file size in bytes."""
        if self._file is None:
            return 0
        self._file.flush()
        return self._file.tell()

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def write(self, msg: CanMessage, direction: str = "Rx"):
        if self._file is None:
            return
        if self._start_time is None:
            self._start_time = msg.timestamp
        self._msg_number += 1
        offset = msg.timestamp - self._start_time
        msg_type = "1" if not msg.is_fd else "FD"
        can_id = f"{msg.arbitration_id:04X}"
        dlc = len(msg.data)
        data_str = " ".join(f"{b:02X}" for b in msg.data)
        self._file.write(
            f"  {self._msg_number:>6})  {offset:>12.3f} {msg_type:>2}  "
            f"{can_id}  {direction:<2}  d {dlc:>2}  {data_str}\n"
        )

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
