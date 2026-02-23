"""Trace file reader for CAN recordings.

Supports streaming read from:

- TRC (PEAK ASCII)
- BLF (Vector BLF via python-can)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import can

from cangui.can_message import CanMessage


@dataclass
class TraceEntry:
    """A single decoded CAN frame from a trace file."""

    number: int
    time_offset: float
    message: CanMessage
    direction: str


_LINE_RE = re.compile(
    r"\s*(\d+)\)\s+"
    r"([\d.]+)\s+"
    r"(\S+)\s+"
    r"([0-9A-Fa-f]+)\s+"
    r"(Rx|Tx)\s+"
    r"d\s+"
    r"(\d+)\s+"
    r"((?:[0-9A-Fa-f]{2}\s?)*)"
)


def detect_trace_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".blf":
        return "blf"
    return "trc"


class TraceReader:
    """Reads TRC/BLF traces with both streaming and list APIs."""

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

    def has_entries(self) -> bool:
        for _ in self.iter_entries():
            return True
        return False

    def iter_entries(self) -> Iterator[TraceEntry]:
        fmt = detect_trace_format(self._path)
        if fmt == "blf":
            yield from self._iter_blf()
            return
        yield from self._iter_trc()

    def load(self) -> list[TraceEntry]:
        self._entries = list(self.iter_entries())
        return self._entries

    def _iter_blf(self) -> Iterator[TraceEntry]:
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
                yield TraceEntry(
                    number=number,
                    time_offset=time_offset,
                    message=can_msg,
                    direction="Rx",
                )

    def _iter_trc(self) -> Iterator[TraceEntry]:
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
                yield TraceEntry(
                    number=number,
                    time_offset=time_offset,
                    message=msg,
                    direction=direction,
                )
