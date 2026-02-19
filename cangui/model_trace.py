import queue as _stdlib_queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, Signal, QThread

from cangui.can_message import CanMessage
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


COLUMNS = ["#", "Time", "Bus", "CAN-ID", "Dir", "Type", "DLC", "Data"]

DISPLAY_BUFFER_SIZE = 100_000
MAX_FILE_SIZE = 1_000_000_000  # 1 GB


class _DiskWriterThread(QThread):
    """Background thread: owns the trace file and handles all disk I/O.

    The main thread hands off messages via a lock-free queue and never
    touches the file directly, so disk latency cannot stall the UI.
    """

    file_changed = Signal(str)  # emitted from this thread; Qt queues delivery to main thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q: _stdlib_queue.SimpleQueue = _stdlib_queue.SimpleQueue()

    # -- Commands called from the main thread (non-blocking) --

    def cmd_start(self, folder: Path, fmt: TraceFormat, base_name: str):
        self._q.put(("start", folder, fmt, base_name))

    def cmd_write(self, msg: CanMessage, direction: str):
        self._q.put(("write", msg, direction))

    def cmd_stop(self):
        self._q.put(("stop",))

    def cmd_sync(self):
        """Block the calling thread until all prior commands have been processed."""
        event = threading.Event()
        self._q.put(("sync", event))
        event.wait(timeout=10.0)

    def cmd_quit(self):
        self._q.put(("quit",))
        self.wait(5000)

    # -- Thread body --

    def run(self):
        writer: TraceWriter | None = None
        file_index = 0
        base_name = ""
        trace_folder: Path | None = None
        trace_format = TraceFormat.TRC

        while True:
            item = self._q.get()
            op = item[0]

            if op == "write":
                if writer is None:
                    continue
                _, msg, direction = item
                if writer.file_size >= MAX_FILE_SIZE:
                    writer.close()
                    file_index += 1
                    path = trace_folder / f"{base_name}_{file_index:03d}.{trace_format.value}"
                    writer = create_trace_writer(path, trace_format)
                    writer.open()
                    self.file_changed.emit(str(path))
                writer.write(msg, direction=direction)

            elif op == "start":
                _, trace_folder, trace_format, base_name = item
                if writer is not None:
                    writer.close()
                file_index = 0
                trace_folder.mkdir(parents=True, exist_ok=True)
                path = trace_folder / f"{base_name}.{trace_format.value}"
                writer = create_trace_writer(path, trace_format)
                writer.open()
                self.file_changed.emit(str(path))

            elif op == "stop":
                if writer is not None:
                    writer.close()
                    writer = None
                self.file_changed.emit("")

            elif op == "sync":
                _, event = item
                event.set()

            elif op == "quit":
                if writer is not None:
                    writer.close()
                break


class TraceModel(QAbstractTableModel):
    file_changed = Signal(str)      # current trace file path (or "" when closed)
    entries_committed = Signal()    # emitted after staged entries are committed to display
    rate_updated = Signal(int)      # messages per second

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: deque[TraceEntry] = deque(maxlen=DISPLAY_BUFFER_SIZE)
        # Staging list: TraceEntry objects waiting to be committed to the display model.
        self._staged: list[TraceEntry] = []
        self._msg_number = 0
        self._start_time: float | None = None
        self._pending: list[CanMessage] = []
        self._pending_directions: list[str] = []
        self._recording = False

        # Trace settings (set before start() is called)
        self._trace_folder: Path | None = None
        self._trace_format = TraceFormat.TRC
        self._current_file: str = ""

        # Background disk-writer thread
        self._disk_thread = _DiskWriterThread(self)
        self._disk_thread.file_changed.connect(self._on_disk_file_changed)
        self._disk_thread.start()

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
        self._rate_window_start = time.monotonic()

    def _on_disk_file_changed(self, path: str):
        self._current_file = path
        self.file_changed.emit(path)

    @property
    def recording(self) -> bool:
        return self._recording

    def set_trace_folder(self, folder: Path | None):
        self._trace_folder = folder

    def set_trace_format(self, fmt: str):
        try:
            self._trace_format = TraceFormat(fmt)
        except ValueError:
            self._trace_format = TraceFormat.TRC

    @property
    def current_file(self) -> str:
        return self._current_file

    def flush_all(self):
        """Flush all pending/staged data into entries (call before iterating entries)."""
        self._flush()
        self._commit_staged()

    def flush_disk(self):
        """Block until all pending disk writes have completed."""
        self._disk_thread.cmd_sync()

    @property
    def message_count(self) -> int:
        return self._msg_number

    @property
    def entries(self) -> deque[TraceEntry]:
        return self._entries

    def start(self):
        self._recording = True
        if self._trace_folder is not None:
            base_name = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            self._disk_thread.cmd_start(self._trace_folder, self._trace_format, base_name)

    def pause(self):
        self._recording = False

    def stop(self):
        self._recording = False
        self._flush()           # drain _pending → disk queue + _staged
        self._disk_thread.cmd_stop()  # queued after all write commands

    def shutdown(self):
        """Stop the background disk-writer thread. Call once on application exit."""
        self._batch_timer.stop()
        self._view_timer.stop()
        self._flush()
        self._disk_thread.cmd_quit()

    def clear(self):
        self.beginResetModel()
        self._entries.clear()
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

    # -- Batching / view updates --

    def _flush(self):
        """Fast timer (50ms): convert pending CAN messages to TraceEntry objects.

        All disk I/O is handed off to _DiskWriterThread via a non-blocking queue,
        so this method never blocks the main thread on file operations.
        """
        if not self._pending:
            return
        batch = self._pending
        directions = self._pending_directions
        self._pending = []
        self._pending_directions = []

        # Rate tracking
        now = time.monotonic()
        self._rate_count += len(batch)
        elapsed = now - self._rate_window_start
        if elapsed >= 1.0:
            self.rate_updated.emit(int(self._rate_count / elapsed))
            self._rate_count = 0
            self._rate_window_start = now

        for msg, direction in zip(batch, directions):
            if self._start_time is None:
                self._start_time = msg.timestamp
            self._msg_number += 1
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
            )
            # Hand off to the disk writer thread — non-blocking.
            self._disk_thread.cmd_write(msg, direction)
            self._staged.append(entry)

    def _commit_staged(self):
        """Slow timer (200ms): commit staged entries to the display model."""
        if not self._staged:
            return
        staged = self._staged
        self._staged = []

        old_size = len(self._entries)
        count = len(staged)

        if old_size + count <= DISPLAY_BUFFER_SIZE:
            # Buffer has room — inform the view of the exact rows added.
            self.beginInsertRows(QModelIndex(), old_size, old_size + count - 1)
            self._entries.extend(staged)
            self.endInsertRows()
        else:
            # Buffer is full (or will overflow): the deque drops old entries from
            # the front.  A full reset is cheaper than claiming all 100K rows changed.
            self.beginResetModel()
            self._entries.extend(staged)
            self.endResetModel()

        self.entries_committed.emit()

    # -- QAbstractTableModel interface --

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
        if row < 0 or row >= len(self._entries):
            return None
        entry = self._entries[row]
        match index.column():
            case 0: return entry.number
            case 1: return f"{entry.timestamp:.3f}"
            case 2: return entry.bus
            case 3: return (f"{entry.can_id:08X}" if entry.is_extended_id
                            else f"{entry.can_id:03X}")
            case 4: return entry.direction
            case 5: return entry.frame_type
            case 6: return entry.dlc
            case 7: return " ".join(f"{b:02X}" for b in entry.data)
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
