from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, Signal

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder
from cangui.trace_writer import TraceWriter, TraceFormat, create_trace_writer


@dataclass
class TraceEntry:
    number: int
    timestamp: float
    bus: int
    can_id: int
    is_extended_id: bool
    direction: str
    frame_type: str
    dlc: int
    data: bytes
    decoded: str = ""


COLUMNS = ["#", "Time", "Bus", "CAN-ID", "Dir", "Type", "DLC", "Data", "Decoded"]

DISPLAY_BUFFER_SIZE = 100_000
MAX_FILE_SIZE = 1_000_000_000  # 1 GB


class TraceModel(QAbstractTableModel):
    file_changed = Signal(str)  # emitted with current trace file path
    entries_committed = Signal()  # emitted after staged entries are committed to display
    rate_updated = Signal(int)  # messages per second

    def __init__(self, decoder: SignalDecoder | None = None, parent=None):
        super().__init__(parent)
        self._entries: deque[TraceEntry] = deque(maxlen=DISPLAY_BUFFER_SIZE)
        self._display_rows: deque[tuple] = deque(maxlen=DISPLAY_BUFFER_SIZE)
        self._staged: list[tuple[TraceEntry, tuple]] = []
        self._msg_number = 0
        self._start_time: float | None = None
        self._pending: list[CanMessage] = []
        self._pending_directions: list[str] = []
        self._recording = False
        self._decoder = decoder

        # Trace folder — set by main_window from project path
        self._trace_folder: Path | None = None

        # Disk writer state
        self._writer: TraceWriter | None = None
        self._file_index = 0
        self._base_name = ""
        self._trace_format = TraceFormat.TRC

        # Fast timer: convert pending messages to TraceEntry objects (data capture)
        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(50)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

        # Slow timer: commit staged entries to the model (screen update)
        self._view_timer = QTimer(self)
        self._view_timer.setInterval(200)
        self._view_timer.timeout.connect(self._commit_staged)
        self._view_timer.start()

        # Rate tracking
        self._rate_count = 0
        self._rate_window_start = 0.0

    @property
    def recording(self) -> bool:
        return self._recording

    def set_trace_folder(self, folder: Path | None):
        """Set the folder where trace files are created."""
        self._trace_folder = folder

    def set_trace_format(self, fmt: str):
        """Set the trace format ('trc' or 'blf')."""
        try:
            self._trace_format = TraceFormat(fmt)
        except ValueError:
            self._trace_format = TraceFormat.TRC

    @property
    def current_file(self) -> str:
        """Return the path of the current trace file, or empty string."""
        if self._writer is not None:
            return str(self._writer.path)
        return ""

    def flush_all(self):
        """Force all pending/staged data into entries (call before saving)."""
        self._flush()
        self._commit_staged()

    @property
    def message_count(self) -> int:
        """Total number of messages recorded (may exceed display buffer)."""
        return self._msg_number

    @property
    def entries(self) -> deque[TraceEntry]:
        return self._entries

    def start(self):
        self._recording = True
        self._open_trace_file()

    def pause(self):
        self._recording = False

    def stop(self):
        self._recording = False
        self._flush()  # write remaining pending messages to disk
        self._close_trace_file()

    def clear(self):
        self.beginResetModel()
        self._entries.clear()
        self._display_rows.clear()
        self._staged.clear()
        self._msg_number = 0
        self._start_time = None
        self._pending.clear()
        self._pending_directions.clear()
        self.endResetModel()

    def on_message(self, msg: CanMessage, direction: str = "Rx"):
        if not self._recording:
            return
        self._pending.append(msg)
        self._pending_directions.append(direction)

    def on_messages(self, messages: list[CanMessage]):
        if not self._recording:
            return
        self._pending.extend(messages)
        self._pending_directions.extend("Rx" for _ in messages)

    # -- Disk file management --

    def _open_trace_file(self):
        """Open a new trace file with ISO 8601 timestamp."""
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

    def _roll_trace_file(self):
        """Close current file and open a new one with incremented index."""
        if self._writer is not None:
            self._writer.close()
        self._file_index += 1
        ext = self._trace_format.value
        path = self._trace_folder / f"{self._base_name}_{self._file_index:03d}.{ext}"
        self._writer = create_trace_writer(path, self._trace_format)
        self._writer.open()
        self.file_changed.emit(str(path))

    def _close_trace_file(self):
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self.file_changed.emit("")

    def _write_entry_to_disk(self, entry: TraceEntry):
        """Write a single trace entry to the current file, rolling if needed."""
        if self._writer is None:
            return
        if self._writer.file_size >= MAX_FILE_SIZE:
            self._roll_trace_file()
        msg = CanMessage(
            arbitration_id=entry.can_id,
            data=entry.data,
            is_extended_id=entry.is_extended_id,
            is_fd=entry.frame_type == "FD",
            dlc=entry.dlc,
            timestamp=entry.timestamp + (self._start_time or 0),
            bus=entry.bus,
        )
        self._writer.write(msg, direction=entry.direction)

    # -- Batching / view updates --

    def _decode_message(self, arb_id: int, data: bytes) -> str:
        if self._decoder is None:
            return ""
        decoded = self._decoder.decode(arb_id, data)
        if not decoded:
            return ""
        parts = []
        for sig in decoded:
            if sig.unit:
                parts.append(f"{sig.name}={sig.display_value} {sig.unit}")
            else:
                parts.append(f"{sig.name}={sig.display_value}")
        return "  ".join(parts)

    @staticmethod
    def _format_display(entry: TraceEntry) -> tuple:
        """Pre-format a TraceEntry into a tuple of display values."""
        can_id_str = (
            f"{entry.can_id:08X}" if entry.is_extended_id else f"{entry.can_id:03X}"
        )
        data_str = " ".join(f"{b:02X}" for b in entry.data)
        return (
            entry.number,
            f"{entry.timestamp:.3f}",
            entry.bus,
            can_id_str,
            entry.direction,
            entry.frame_type,
            entry.dlc,
            data_str,
            entry.decoded,
        )

    def _flush(self):
        """Fast timer (50ms): convert pending CAN messages to TraceEntry objects
        and write them to disk immediately."""
        if not self._pending:
            return
        batch = self._pending
        directions = self._pending_directions
        self._pending = []
        self._pending_directions = []

        # Update rate counter
        import time
        now = time.monotonic()
        self._rate_count += len(batch)
        elapsed = now - self._rate_window_start
        if elapsed >= 1.0:
            rate = int(self._rate_count / elapsed)
            self.rate_updated.emit(rate)
            self._rate_count = 0
            self._rate_window_start = now

        for msg, direction in zip(batch, directions):
            if self._start_time is None:
                self._start_time = msg.timestamp
            self._msg_number += 1
            decoded = self._decode_message(msg.arbitration_id, msg.data)
            entry = TraceEntry(
                number=self._msg_number,
                timestamp=msg.timestamp - self._start_time,
                bus=msg.bus,
                can_id=msg.arbitration_id,
                is_extended_id=msg.is_extended_id,
                direction=direction,
                frame_type=msg.frame_type,
                dlc=msg.dlc,
                data=msg.data,
                decoded=decoded,
            )
            self._write_entry_to_disk(entry)
            self._staged.append((entry, self._format_display(entry)))

    def _commit_staged(self):
        """Slow timer (500ms): commit staged entries to the model for display."""
        if not self._staged:
            return
        staged = self._staged
        self._staged = []

        new_entries = [pair[0] for pair in staged]
        new_display = [pair[1] for pair in staged]

        old_size = len(self._entries)
        count = len(new_entries)

        if old_size >= DISPLAY_BUFFER_SIZE:
            # Buffer already full — row count stays the same.
            # Use dataChanged instead of beginResetModel to avoid
            # tearing down all view state (selection, scroll, editors)
            # every 500ms.
            self._entries.extend(new_entries)
            self._display_rows.extend(new_display)
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._entries) - 1, self.columnCount() - 1),
            )
        elif old_size + count <= DISPLAY_BUFFER_SIZE:
            # Buffer growing, won't overflow
            self.beginInsertRows(QModelIndex(), old_size, old_size + count - 1)
            self._entries.extend(new_entries)
            self._display_rows.extend(new_display)
            self.endInsertRows()
        else:
            # Transition: buffer fills mid-batch (one-time)
            self.beginResetModel()
            self._entries.extend(new_entries)
            self._display_rows.extend(new_display)
            self.endResetModel()

        self.entries_committed.emit()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = index.row()
        if row < 0 or row >= len(self._display_rows):
            return None
        return self._display_rows[row][index.column()]

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
