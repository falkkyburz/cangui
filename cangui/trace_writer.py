"""Trace writer for CAN messages.

Provides a format enumeration, an ASCII TRC writer, and a factory function
for creating the appropriate writer based on the desired output format.

Supported formats:
    - TRC: PEAK-compatible ASCII trace format (version 1.1).
    - BLF: Vector Binary Logging Format (delegated to BlfTraceWriter).
"""

from datetime import datetime
from enum import Enum
from pathlib import Path

from cangui.can_message import CanMessage


class TraceFormat(Enum):
    """Enumeration of supported CAN trace file formats.

    Attributes:
        TRC: PEAK-compatible ASCII text format with ``.trc`` extension.
        BLF: Vector Binary Logging Format with ``.blf`` extension.
    """

    TRC = "trc"
    BLF = "blf"


class TraceWriter:
    """Writes CAN messages to PEAK-compatible ASCII TRC files.

    The file is written in PEAK TRC version 1.1 format.  Each message is
    recorded with a sequential message number, a millisecond time offset
    relative to the first message, a type token (``1`` for classic CAN,
    ``FD`` for CAN-FD), the arbitration ID in hexadecimal, the direction
    (``Rx`` / ``Tx``), the DLC, and the data bytes in hex.

    Example usage::

        with TraceWriter("/tmp/capture.trc") as writer:
            for msg in messages:
                writer.write(msg, direction="Rx")

    Attributes:
        _path: Resolved path to the output TRC file.
        _file: Open file handle while recording, ``None`` otherwise.
        _start_time: Timestamp of the first written message, used as the
            time-offset reference.
        _msg_number: Running count of messages written so far.
    """

    def __init__(self, path: str | Path):
        """Initialise the writer without opening the file.

        Args:
            path: Destination path for the TRC file.  Parent directories
                are created automatically when :meth:`open` is called.
        """
        self._path = Path(path)
        self._file = None
        self._start_time: float | None = None
        self._msg_number = 0

    @property
    def path(self) -> Path:
        """Resolved path to the output TRC file."""
        return self._path

    @property
    def message_count(self) -> int:
        """Number of messages written since the file was opened."""
        return self._msg_number

    def open(self):
        """Open the TRC file and write the PEAK-format file header.

        Creates any missing parent directories before opening the file.
        The header contains the file version, the wall-clock start time,
        and a human-readable column legend.

        Raises:
            OSError: If the file cannot be created or written.
        """
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
        """Current size of the TRC file in bytes.

        Flushes the write buffer before querying the file position so that
        the returned value reflects all data written so far.  Returns ``0``
        when the file is not open.
        """
        if self._file is None:
            return 0
        self._file.flush()
        return self._file.tell()

    @property
    def is_open(self) -> bool:
        """``True`` while the underlying file handle is open."""
        return self._file is not None

    def write(self, msg: CanMessage, direction: str = "Rx"):
        """Append a single CAN message to the TRC file.

        The time offset is computed relative to the timestamp of the first
        message written after :meth:`open` is called.  Does nothing if the
        file is not open.

        Args:
            msg: The CAN message to record.  Must have ``timestamp``,
                ``arbitration_id``, ``data``, and ``is_fd`` attributes.
            direction: Transfer direction label written to the file.
                Typically ``"Rx"`` or ``"Tx"``.  Defaults to ``"Rx"``.
        """
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
        """Close the TRC file and release the file handle.

        Safe to call even when the file is already closed.
        """
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        """Open the file when used as a context manager.

        Returns:
            This :class:`TraceWriter` instance.
        """
        self.open()
        return self

    def __exit__(self, *args):
        """Close the file on context-manager exit."""
        self.close()


def create_trace_writer(path: str | Path, fmt: TraceFormat = TraceFormat.TRC) -> TraceWriter:
    """Factory that creates the appropriate trace writer for *fmt*.

    Args:
        path: Destination file path.  The extension should match *fmt*
            (``.trc`` or ``.blf``).
        fmt: Desired output format.  Defaults to :attr:`TraceFormat.TRC`.

    Returns:
        A :class:`TraceWriter` instance for TRC format, or a
        :class:`~cangui.trace_writer_blf.BlfTraceWriter` instance for BLF
        format.
    """
    if fmt == TraceFormat.BLF:
        from cangui.trace_writer_blf import BlfTraceWriter
        return BlfTraceWriter(path)
    return TraceWriter(path)
