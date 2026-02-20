"""Plot trace service for recording filtered CAN trace files alongside live plots.

This module provides ``PlotTraceService``, which listens to the same CAN message
stream as ``PlotDataService`` but writes only the frames belonging to plotted
signals to disk in a configurable trace format (TRC, ASC, etc.).

File management
---------------
* Files are created in the configured trace folder with an ISO 8601 timestamp
  as the base name (e.g. ``2026-02-20T14-30-00.trc``).
* When a file reaches ``MAX_FILE_SIZE`` (1 GB) it is closed and a new one is
  opened with a three-digit sequence suffix
  (e.g. ``2026-02-20T14-30-00_001.trc``).
* A ``file_changed`` signal notifies the UI with the current file path so the
  status bar can display it; an empty string is emitted when recording stops.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from cangui.can_message import CanMessage
from cangui.trace_writer import TraceWriter, TraceFormat, create_trace_writer


MAX_FILE_SIZE = 1_000_000_000  # 1 GB
"""Maximum size in bytes of a single trace file before a roll-over occurs."""


class PlotTraceService(QObject):
    """Writes filtered trace files containing only messages for plotted signals.

    The service maintains a set of watched arbitration IDs (populated by the
    plot layer) and silently discards any CAN message whose ID is not in that
    set.  This keeps trace files small and directly correlated to the signals
    visible in the plot window.

    File roll-over is checked on every write; when the current file reaches
    ``MAX_FILE_SIZE`` a new numbered file is opened automatically without
    dropping any messages.

    Signals:
        file_changed: Emitted with the path of the newly opened trace file
            whenever a file is created (start or roll-over), and with an empty
            string when recording stops.
        recording_changed: Emitted with ``True`` when recording starts and
            ``False`` when it stops.
    """

    file_changed = Signal(str)
    """Emitted with the absolute path of the active trace file, or ``""`` when closed."""

    recording_changed = Signal(bool)
    """Emitted with the new recording state whenever ``start()`` or ``stop()`` is called."""

    def __init__(self, parent=None):
        """Initialise the service in a stopped, unconfigured state.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._watched_arb_ids: set[int] = set()
        self._recording = False
        self._writer: TraceWriter | None = None
        self._trace_folder: Path | None = None
        self._trace_format = TraceFormat.TRC
        self._base_name = ""
        self._file_index = 0

    @property
    def recording(self) -> bool:
        """Return whether the service is currently writing to a trace file.

        Returns:
            ``True`` between a ``start()`` and ``stop()`` call, ``False``
            otherwise.
        """
        return self._recording

    def set_trace_folder(self, folder: Path | None):
        """Set the directory where trace files will be created.

        Must be called before ``start()`` for recording to succeed.  If the
        folder does not exist it will be created (including any missing parent
        directories) when recording starts.

        Args:
            folder: Destination directory as a ``Path``, or ``None`` to
                disable file output (``start()`` will be a no-op if this is
                ``None``).
        """
        self._trace_folder = folder

    def set_trace_format(self, fmt: str):
        """Set the trace file format by extension string.

        Args:
            fmt: Format identifier string matching a ``TraceFormat`` enum value
                (e.g. ``"trc"`` or ``"asc"``).  If the string is not
                recognised the format falls back to ``TraceFormat.TRC``.
        """
        try:
            self._trace_format = TraceFormat(fmt)
        except ValueError:
            self._trace_format = TraceFormat.TRC

    def set_watched_arb_ids(self, arb_ids: set[int]):
        """Replace the complete set of watched arbitration IDs.

        Args:
            arb_ids: Set of CAN arbitration IDs whose messages should be
                written to the trace file.  Replaces any previously configured
                set entirely.
        """
        self._watched_arb_ids = arb_ids

    def add_arb_id(self, arb_id: int):
        """Add a single arbitration ID to the watched set.

        Args:
            arb_id: CAN arbitration ID to start recording.  If the ID is
                already in the set this is a no-op.
        """
        self._watched_arb_ids.add(arb_id)

    def remove_arb_id(self, arb_id: int):
        """Remove a single arbitration ID from the watched set.

        Args:
            arb_id: CAN arbitration ID to stop recording.  If the ID is not
                in the set this is a no-op (``discard`` semantics).
        """
        self._watched_arb_ids.discard(arb_id)

    def start(self):
        """Begin recording to a new trace file.

        Opens a new file in the configured trace folder using the current
        timestamp as the base filename.  If recording is already active this
        method is a no-op.  If no trace folder has been set, recording is
        marked active but no file is opened.

        Emits:
            file_changed: With the path of the newly created file.
            recording_changed: With ``True``.
        """
        if self._recording:
            return
        self._recording = True
        self._open_file()
        self.recording_changed.emit(True)

    def stop(self):
        """Stop recording and close the current trace file.

        If recording is not active this method is a no-op.  The active writer
        is flushed and closed before the signal is emitted.

        Emits:
            file_changed: With an empty string to indicate no file is active.
            recording_changed: With ``False``.
        """
        if not self._recording:
            return
        self._recording = False
        self._close_file()
        self.recording_changed.emit(False)

    def on_message(self, msg: CanMessage):
        """Write a single CAN message to the trace file if its ID is watched.

        Args:
            msg: Received CAN message.  Only ``arbitration_id``, ``data``,
                and ``timestamp`` are used by the underlying writer.
        """
        if not self._recording:
            return
        if msg.arbitration_id not in self._watched_arb_ids:
            return
        self._write(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Write a batch of CAN messages, filtering to watched IDs only.

        Args:
            messages: List of received CAN messages.  Messages whose
                arbitration IDs are not in the watched set are silently
                discarded.
        """
        if not self._recording or not self._watched_arb_ids:
            return
        for msg in messages:
            if msg.arbitration_id in self._watched_arb_ids:
                self._write(msg)

    def _write(self, msg: CanMessage):
        """Write one message to the active writer, rolling the file if needed.

        Checks the writer's ``file_size`` attribute before each write; if the
        limit has been reached ``_roll_file()`` is called first.

        Args:
            msg: CAN message to serialise.  Written with ``direction="Rx"``
                because all messages are received frames from the bus.
        """
        if self._writer is None:
            return
        if self._writer.file_size >= MAX_FILE_SIZE:
            self._roll_file()
        self._writer.write(msg, direction="Rx")

    def _open_file(self):
        """Create and open the first trace file for the current recording session.

        The filename is derived from the current wall-clock time formatted as
        ``YYYY-MM-DDTHH-MM-SS.<ext>``.  The trace folder is created if it does
        not already exist.  Does nothing when ``_trace_folder`` is ``None``.

        Emits:
            file_changed: With the absolute path of the newly opened file.
        """
        if self._trace_folder is None:
            return
        self._trace_folder.mkdir(parents=True, exist_ok=True)
        self._base_name = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self._file_index = 0
        ext = self._trace_format.value
        path = self._trace_folder / f"{self._base_name}.{ext}"
        self._writer = create_trace_writer(path, self._trace_format)
        self._writer.open()
        self.file_changed.emit(str(path))

    def _roll_file(self):
        """Close the current file and open the next numbered roll-over file.

        The new filename appends a zero-padded three-digit index to the base
        name (e.g. ``…_001.trc``, ``…_002.trc``).  No messages are dropped
        during the transition.

        Emits:
            file_changed: With the absolute path of the newly opened file.
        """
        if self._writer is not None:
            self._writer.close()
        self._file_index += 1
        ext = self._trace_format.value
        path = self._trace_folder / f"{self._base_name}_{self._file_index:03d}.{ext}"
        self._writer = create_trace_writer(path, self._trace_format)
        self._writer.open()
        self.file_changed.emit(str(path))

    def _close_file(self):
        """Flush and close the active trace writer.

        Sets ``_writer`` to ``None`` so subsequent ``_write()`` calls are safe
        no-ops.

        Emits:
            file_changed: With an empty string to signal that no file is open.
        """
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self.file_changed.emit("")

    @property
    def current_file(self) -> str:
        """Return the absolute path of the currently open trace file, or an empty string.

        Returns:
            Absolute path as a string when a writer is active, or ``""`` when
            recording is stopped or no file has been opened yet.
        """
        if self._writer is not None:
            return str(self._writer.path)
        return ""
