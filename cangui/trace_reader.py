"""Trace file reader for CAN recordings.

Supports two input formats:

- **TRC** – PEAK-compatible ASCII text format produced by PEAK PCAN-Explorer
  and by :class:`~cangui.trace_writer.TraceWriter`.
- **BLF** – Vector Binary Logging Format read via ``python-can``'s
  :class:`can.BLFReader`.

The public API is :class:`TraceReader`, which auto-detects the format from
the file extension and returns a list of :class:`TraceEntry` records.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import can

from cangui.can_message import CanMessage


@dataclass
class TraceEntry:
    """A single decoded CAN frame from a trace file.

    Attributes:
        number: Sequential 1-based message index within the trace file.
        time_offset: Time in seconds relative to the first message in the
            file (i.e., the time-offset column from the TRC format).
        message: The decoded CAN frame as a
            :class:`~cangui.can_message.CanMessage`.
        direction: Transfer direction; ``"Rx"`` or ``"Tx"``.  BLF files
            do not carry direction information, so BLF entries always use
            ``"Rx"``.
    """

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
"""Pre-compiled regular expression for matching a single TRC data line."""


def detect_trace_format(path: str | Path) -> str:
    """Detect the trace file format from the file extension.

    Args:
        path: Path to the trace file.

    Returns:
        ``"blf"`` if the file has a ``.blf`` extension (case-insensitive),
        ``"trc"`` for every other extension.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".blf":
        return "blf"
    return "trc"


class TraceReader:
    """Reads trace files (TRC or BLF) into a list of TraceEntry objects.

    The format is detected automatically from the file extension via
    :func:`detect_trace_format`.  After calling :meth:`load`, the decoded
    entries are available via :attr:`entries`.

    Example usage::

        reader = TraceReader("/path/to/capture.trc")
        entries = reader.load()
        for entry in entries:
            print(entry.time_offset, entry.message.arbitration_id)

    Attributes:
        _path: Resolved path to the trace file.
        _entries: List of decoded :class:`TraceEntry` records populated by
            :meth:`load`.
    """

    def __init__(self, path: str | Path):
        """Initialise the reader.

        Args:
            path: Path to the TRC or BLF trace file to read.
        """
        self._path = Path(path)
        self._entries: list[TraceEntry] = []

    @property
    def path(self) -> Path:
        """Resolved path to the trace file."""
        return self._path

    @property
    def entries(self) -> list[TraceEntry]:
        """List of decoded trace entries, populated after :meth:`load` returns."""
        return self._entries

    @property
    def duration(self) -> float:
        """Total duration of the trace in seconds.

        Computed as the :attr:`TraceEntry.time_offset` of the last entry.
        Returns ``0.0`` if no entries have been loaded yet.
        """
        if not self._entries:
            return 0.0
        return self._entries[-1].time_offset

    def load(self) -> list[TraceEntry]:
        """Load and parse the trace file, returning all decoded entries.

        Auto-detects the file format from the extension and delegates to
        :meth:`_load_blf` or :meth:`_load_trc` accordingly.  Previous
        entries are cleared before loading.

        Returns:
            A list of :class:`TraceEntry` objects in file order.

        Raises:
            FileNotFoundError: If the trace file does not exist.
            OSError: If the file cannot be opened for reading.
            can.CanError: If a BLF file is malformed or unreadable.
        """
        fmt = detect_trace_format(self._path)
        if fmt == "blf":
            return self._load_blf()
        return self._load_trc()

    def _load_blf(self) -> list[TraceEntry]:
        """Parse a Vector BLF file into :class:`TraceEntry` records.

        Uses :class:`can.BLFReader` from ``python-can`` to iterate over all
        messages.  The time offset of each message is computed relative to
        the absolute timestamp of the first message in the file.

        All entries are tagged with direction ``"Rx"`` because the BLF
        format does not carry per-frame direction information.

        Returns:
            A list of :class:`TraceEntry` objects in file order.
        """
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
        """Parse a PEAK ASCII TRC file into :class:`TraceEntry` records.

        Lines beginning with ``;`` are treated as comments and skipped.
        Data lines are matched against :data:`_LINE_RE`.  Unrecognised lines
        are silently ignored.

        The CAN ID is used to infer the ``is_extended_id`` flag: any ID
        greater than ``0x7FF`` is treated as a 29-bit extended identifier.

        Returns:
            A list of :class:`TraceEntry` objects in file order.
        """
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
