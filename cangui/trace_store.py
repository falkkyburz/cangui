"""Fixed-size append-only trace store used by the virtualized trace view.

The store is optimized for random row access:

- Records are fixed-size binary blobs.
- Row offsets are O(1): ``offset = HEADER_SIZE + row * RECORD_SIZE``.
- The reader never loads the whole file; it reads only requested row ranges.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from cangui.model_trace import TraceEntry


_MAGIC = b"CANGUI01"
_VERSION = 2
_HEADER_STRUCT = struct.Struct("<8sI20x")
_HEADER_SIZE = _HEADER_STRUCT.size

# 96 bytes per record:
# timestamp_ns (Q), can_id (I), bus (H), flags (B), direction (B),
# frame_type (B), dlc (B), length (B), data (64s), padding (13x)
_RECORD_STRUCT = struct.Struct("<QIHBBBBB64s13x")
_RECORD_SIZE = _RECORD_STRUCT.size

_FLAG_EXTENDED_ID = 0x01

_DIRECTION_RX = 0
_DIRECTION_TX = 1

_FRAME_DATA = 0
_FRAME_RTR = 1
_FRAME_FD = 2
_FRAME_ERROR = 3


@dataclass(frozen=True)
class TraceStoreRecord:
    """Binary row representation for on-disk storage."""

    timestamp_ns: int
    can_id: int
    bus: int
    flags: int
    direction: int
    frame_type: int
    dlc: int
    length: int
    data: bytes

    @classmethod
    def from_entry(cls, entry: "TraceEntry") -> "TraceStoreRecord":
        flags = _FLAG_EXTENDED_ID if entry.is_extended_id else 0
        direction = _DIRECTION_TX if entry.direction == "Tx" else _DIRECTION_RX
        frame_type = _FRAME_DATA
        if entry.frame_type == "RTR":
            frame_type = _FRAME_RTR
        elif entry.frame_type == "FD":
            frame_type = _FRAME_FD
        elif entry.frame_type == "Error":
            frame_type = _FRAME_ERROR

        payload = entry.data[:64]
        if len(payload) < 64:
            payload = payload + (b"\x00" * (64 - len(payload)))

        return cls(
            timestamp_ns=max(0, int(entry.timestamp * 1_000_000_000)),
            can_id=entry.can_id,
            bus=max(0, int(entry.bus)),
            flags=flags,
            direction=direction,
            frame_type=frame_type,
            dlc=max(0, min(255, int(entry.dlc))),
            length=max(0, min(64, int(entry.length))),
            data=payload,
        )

    def to_entry(self, row_index: int) -> "TraceEntry":
        from cangui.model_trace import TraceEntry
        direction = "Tx" if self.direction == _DIRECTION_TX else "Rx"
        frame_type = "Data"
        if self.frame_type == _FRAME_RTR:
            frame_type = "RTR"
        elif self.frame_type == _FRAME_FD:
            frame_type = "FD"
        elif self.frame_type == _FRAME_ERROR:
            frame_type = "Error"

        data_len = max(0, min(64, self.length, len(self.data)))
        data = self.data[:data_len]

        return TraceEntry(
            number=row_index + 1,
            timestamp=self.timestamp_ns / 1_000_000_000,
            bus=self.bus,
            can_id=self.can_id,
            is_extended_id=bool(self.flags & _FLAG_EXTENDED_ID),
            direction=direction,
            frame_type=frame_type,
            dlc=self.dlc,
            length=self.length,
            data=data,
        )

    def pack(self) -> bytes:
        return _RECORD_STRUCT.pack(
            self.timestamp_ns,
            self.can_id,
            self.bus,
            self.flags,
            self.direction,
            self.frame_type,
            self.dlc,
            self.length,
            self.data,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "TraceStoreRecord":
        unpacked = _RECORD_STRUCT.unpack(data)
        return cls(
            timestamp_ns=unpacked[0],
            can_id=unpacked[1],
            bus=unpacked[2],
            flags=unpacked[3],
            direction=unpacked[4],
            frame_type=unpacked[5],
            dlc=unpacked[6],
            length=unpacked[7],
            data=unpacked[8],
        )


class TraceStoreWriter:
    """Append-only writer for the fixed-size trace store."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._file is not None

    @property
    def file_size(self) -> int:
        if self._file is None:
            return 0
        self._file.flush()
        return self._file.tell()

    def open(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self._path.exists() or self._path.stat().st_size == 0
        self._file = open(self._path, "ab")
        if new_file:
            self._file.write(_HEADER_STRUCT.pack(_MAGIC, _VERSION))

    def append_entry(self, entry: "TraceEntry"):
        if self._file is None:
            return
        record = TraceStoreRecord.from_entry(entry)
        self._file.write(record.pack())

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def flush(self):
        if self._file is not None:
            self._file.flush()


class TraceStoreReader:
    """Random-access reader for the fixed-size trace store."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _validate_header(self):
        with open(self._path, "rb") as f:
            raw = f.read(_HEADER_SIZE)
        if len(raw) != _HEADER_SIZE:
            raise ValueError("Trace store header is truncated")
        magic, version = _HEADER_STRUCT.unpack(raw)[:2]
        if magic != _MAGIC:
            raise ValueError("Invalid trace store magic")
        if version != _VERSION:
            raise ValueError(f"Unsupported trace store version: {version}")

    def row_count(self) -> int:
        if not self._path.exists():
            return 0
        size = self._path.stat().st_size
        if size < _HEADER_SIZE:
            return 0
        payload = size - _HEADER_SIZE
        return payload // _RECORD_SIZE

    def read_range(self, start_row: int, count: int) -> list[TraceEntry]:
        if count <= 0:
            return []
        if not self._path.exists():
            return []

        self._validate_header()

        total_rows = self.row_count()
        if start_row < 0:
            start_row = 0
        if start_row >= total_rows:
            return []

        end_row = min(total_rows, start_row + count)
        read_rows = end_row - start_row
        offset = _HEADER_SIZE + (start_row * _RECORD_SIZE)

        entries: list[TraceEntry] = []
        with open(self._path, "rb") as f:
            f.seek(offset)
            blob = f.read(read_rows * _RECORD_SIZE)

        for i in range(0, len(blob), _RECORD_SIZE):
            chunk = blob[i:i + _RECORD_SIZE]
            if len(chunk) != _RECORD_SIZE:
                break
            rec = TraceStoreRecord.unpack(chunk)
            row_index = start_row + (i // _RECORD_SIZE)
            entries.append(rec.to_entry(row_index))

        return entries

    def iter_entries(self, chunk_size: int = 2048) -> Iterator[TraceEntry]:
        total = self.row_count()
        row = 0
        while row < total:
            page = self.read_range(row, chunk_size)
            if not page:
                break
            for entry in page:
                yield entry
            row += len(page)


def default_trace_store_name() -> str:
    return "trace.ctb"
