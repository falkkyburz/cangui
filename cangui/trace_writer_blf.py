"""BLF trace writer for CAN messages.

Provides :class:`BlfTraceWriter`, a concrete implementation of
:class:`~cangui.trace_writer.TraceWriter` that persists CAN messages in
Vector's Binary Logging Format (BLF) using the ``python-can`` library.
"""

import can
from pathlib import Path

from cangui.can_message import CanMessage
from cangui.trace_writer import TraceWriter


class BlfTraceWriter(TraceWriter):
    """Writes CAN messages to Vector BLF binary format using python-can.

    BLF (Binary Logging Format) is a compact binary format used by Vector
    tools such as CANalyzer and CANdb++.  This class wraps
    :class:`can.BLFWriter` from the ``python-can`` library and exposes the
    same open/write/close interface as :class:`~cangui.trace_writer.TraceWriter`.

    Each :class:`~cangui.can_message.CanMessage` is converted to a
    :class:`can.Message` before being handed off to the underlying BLF writer.

    Example usage::

        with BlfTraceWriter("/tmp/capture.blf") as writer:
            for msg in messages:
                writer.write(msg, direction="Rx")

    Attributes:
        _path: Resolved destination path for the BLF file.
        _writer: Active :class:`can.BLFWriter` instance, or ``None`` when
            the file is not open.
        _msg_number: Running count of messages written since :meth:`open`.
        _start_time: Timestamp of the first message written, kept for
            internal bookkeeping (mirrors the TRC writer API).
    """

    def __init__(self, path: str | Path):
        """Initialise the writer without opening the BLF file.

        Args:
            path: Destination path for the BLF file.  Parent directories
                are created automatically when :meth:`open` is called.
        """
        self._path = Path(path)
        self._writer: can.BLFWriter | None = None
        self._msg_number = 0
        self._start_time: float | None = None

    @property
    def path(self) -> Path:
        """Resolved path to the destination BLF file."""
        return self._path

    @property
    def message_count(self) -> int:
        """Number of messages written since the file was opened."""
        return self._msg_number

    def open(self):
        """Open the BLF file and initialise the python-can BLFWriter.

        Creates any missing parent directories before opening the writer.

        Raises:
            OSError: If the parent directory cannot be created or the BLF
                file cannot be opened for writing.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = can.BLFWriter(self._path)
        self._msg_number = 0
        self._start_time = None

    @property
    def file_size(self) -> int:
        """Current size of the BLF file on disk in bytes.

        Queries the filesystem directly because BLF is a binary format with
        no equivalent of :py:meth:`io.IOBase.tell`.  Returns ``0`` if the
        file is not open or cannot be stat-ed.
        """
        if self._writer is None:
            return 0
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    @property
    def is_open(self) -> bool:
        """``True`` while the underlying :class:`can.BLFWriter` is active."""
        return self._writer is not None

    def write(self, msg: CanMessage, direction: str = "Rx"):
        """Append a single CAN message to the BLF file.

        Converts the application-level :class:`~cangui.can_message.CanMessage`
        to a :class:`can.Message` and passes it to
        :meth:`can.BLFWriter.on_message_received`.  Does nothing if the file
        is not open.

        The *direction* parameter is accepted for API compatibility with
        :class:`~cangui.trace_writer.TraceWriter` but is not stored in the
        BLF file because the BLF format does not have a direction field.

        Args:
            msg: The CAN message to record.
            direction: Transfer direction (``"Rx"`` or ``"Tx"``).
                Accepted for interface compatibility; not written to the file.
        """
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
        """Finalise and close the BLF file.

        Calls :meth:`can.BLFWriter.stop` to flush internal buffers and write
        the BLF file footer before releasing the writer.  Safe to call even
        when the file is already closed.
        """
        if self._writer is not None:
            self._writer.stop()
            self._writer = None
