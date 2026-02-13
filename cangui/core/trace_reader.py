import re
from dataclasses import dataclass
from pathlib import Path

from cangui.core.can_message import CanMessage


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


class TraceReader:
    """Reads PEAK ASCII TRC files into a list of TraceEntry objects."""

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
