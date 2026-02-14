import re
from dataclasses import dataclass
from pathlib import Path

import can

from cangui.can_message import CanMessage


@dataclass
class TraceEntry:
    number: int
    time_offset: float
    message: CanMessage
    direction: str  # "Rx" or "Tx"


_LINE_RE = re.compile(
    r"\s*(\d+)\)\s+"           # message number
    r"([\d.]+)\s+"             # time offset
    r"(\S+)\s+"                # type (1, FD, etc.)
    r"([0-9A-Fa-f]+)\s+"      # CAN ID
    r"(Rx|Tx)\s+"              # direction
    r"d\s+"                    # 'd' marker
    r"(\d+)\s+"                # DLC
    r"((?:[0-9A-Fa-f]{2}\s?)*)"  # data bytes
)


def detect_trace_format(path: str | Path) -> str:
    """Detect trace file format from extension. Returns 'trc' or 'blf'."""
    suffix = Path(path).suffix.lower()
    if suffix == ".blf":
        return "blf"
    return "trc"


class TraceReader:
    """Reads trace files (TRC or BLF) into a list of TraceEntry objects."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._entries: list[TraceEntry] = []

    @property
    def path(self) -> Path:
        return self._path

    @property
    def entries(self) -> list[TraceEntry]:
        return self._entries

    @property
    def duration(self) -> float:
        if not self._entries:
            return 0.0
        return self._entries[-1].time_offset

    def load(self) -> list[TraceEntry]:
        fmt = detect_trace_format(self._path)
        if fmt == "blf":
            return self._load_blf()
        return self._load_trc()

    def _load_blf(self) -> list[TraceEntry]:
        self._entries.clear()
        start_time = None
        number = 0
        with can.BLFReader(self._path) as reader:
            for msg in reader:
                number += 1
                if start_time is None:
                    start_time = msg.timestamp
                time_offset = msg.timestamp - start_time
                can_msg = CanMessage(
                    arbitration_id=msg.arbitration_id,
                    data=bytes(msg.data),
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                    is_remote_frame=msg.is_remote_frame,
                    is_error_frame=msg.is_error_frame,
                    dlc=msg.dlc,
                    timestamp=msg.timestamp,
                )
                self._entries.append(TraceEntry(
                    number=number,
                    time_offset=time_offset,
                    message=can_msg,
                    direction="Rx",
                ))
        return self._entries

    def _load_trc(self) -> list[TraceEntry]:
        self._entries.clear()
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(";"):
                    continue
                m = _LINE_RE.match(line)
                if m is None:
                    continue
                number = int(m.group(1))
                time_offset = float(m.group(2))
                msg_type = m.group(3)
                can_id = int(m.group(4), 16)
                direction = m.group(5)
                dlc = int(m.group(6))
                data_hex = m.group(7).strip()
                data = bytes.fromhex(data_hex.replace(" ", "")) if data_hex else b""

                msg = CanMessage(
                    arbitration_id=can_id,
                    data=data,
                    is_extended_id=can_id > 0x7FF,
                    is_fd=msg_type == "FD",
                    dlc=dlc,
                    timestamp=time_offset,
                )
                self._entries.append(TraceEntry(
                    number=number,
                    time_offset=time_offset,
                    message=msg,
                    direction=direction,
                ))
        return self._entries
