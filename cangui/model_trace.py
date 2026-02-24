"""Qt trace model with append-only storage + virtualized row access.

Architecture:

- Incoming CAN messages are batched on the GUI thread.
- A background disk thread appends fixed-size records to a trace store file.
- The model keeps only a fixed-size ring buffer in RAM for live view.
- Older rows are read on demand from disk through a paged reader cache.
"""

from __future__ import annotations

import queue as _stdlib_queue
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, Signal, QThread

from cangui.can_message import CanMessage
from cangui.trace_store import TraceStoreReader, TraceStoreWriter
from cangui.trace_writer import TraceFormat, TraceWriter


@dataclass(frozen=True)
class TraceEntry:
    """Immutable snapshot of one CAN frame as displayed in the trace table."""

    number: int
    timestamp: float
    bus: int
    can_id: int
    is_extended_id: bool
    direction: str
    frame_type: str
    dlc: int
    length: int
    data: bytes


COLUMNS = ["#", "Time", "Bus", "ID (hex)", "Dir", "Type", "DLC", "Length", "Data (hex)"]

RING_BUFFER_SIZE = 10_000
PAGE_SIZE = 1_024
PAGE_CACHE_LIMIT = 24
VIEW_UPDATE_INTERVAL_MS = 100


def _convert_trc_to_ctb(trc_path: Path, ctb_path: Path) -> bool:
    """Convert a trace file to CTB (Cangui internal) format.

    Reads all entries from a TRC (PEAK), BLF (Vector), or other supported
    trace file and writes them to CTB format. The CTB file uses a fixed-size
    binary format optimized for random access in the Trace tab.

    Args:
        trc_path: Path to the trace file (.trc, .blf, or other supported format).
        ctb_path: Path where the .ctb file should be written.

    Returns:
        True if conversion succeeded, False otherwise.
    """
    if not trc_path.exists():
        return False

    try:
        from cangui.trace_reader import TraceReader

        reader = TraceReader(trc_path)
        writer = TraceStoreWriter(ctb_path)
        writer.open()

        for entry in reader.iter_entries():
            trace_entry = TraceEntry(
                number=entry.number,
                timestamp=entry.message.timestamp,
                bus=entry.message.bus,
                can_id=entry.message.arbitration_id,
                is_extended_id=entry.message.is_extended_id,
                direction=entry.direction,
                frame_type="FD" if entry.message.is_fd else (
                    "RTR" if entry.message.is_remote_frame else (
                        "Error" if entry.message.is_error_frame else "Data"
                    )
                ),
                dlc=entry.message.dlc or len(entry.message.data),
                length=len(entry.message.data),
                data=entry.message.data,
            )
            writer.append_entry(trace_entry)

        writer.close()
        return True
    except Exception as e:
        print(f"Error converting TRC to CTB: {e}")
        return False


def _export_ctb_to_trc(ctb_path: Path) -> Path | None:
    """Convert a CTB trace file to TRC (PEAK) format.

    Reads all entries from the CTB file and writes them to a corresponding
    .trc file in the same directory. The TRC file uses the same base name.

    Args:
        ctb_path: Path to the .ctb trace file.

    Returns:
        Path to the generated .trc file, or None if export failed.
    """
    if not ctb_path.exists():
        return None

    try:
        trc_path = ctb_path.with_suffix(".trc")
        reader = TraceStoreReader(ctb_path)

        with TraceWriter(trc_path) as writer:
            for entry in reader.iter_entries():
                # Convert TraceEntry to CanMessage-like object for TraceWriter
                msg = CanMessage(
                    arbitration_id=entry.can_id,
                    data=entry.data,
                    is_extended_id=entry.is_extended_id,
                    is_fd=entry.frame_type == "FD",
                    is_remote_frame=entry.frame_type == "RTR",
                    is_error_frame=entry.frame_type == "Error",
                    is_rx=entry.direction == "Rx",
                    dlc=entry.dlc,
                    timestamp=entry.timestamp,
                    bus=entry.bus,
                )
                direction = "Rx" if entry.direction == "Rx" else "Tx"
                writer.write(msg, direction=direction)

        return trc_path
    except Exception as e:
        print(f"Error exporting CTB to TRC: {e}")
        return None


class _DiskWriterThread(QThread):
    """Background thread that owns trace-store file I/O."""

    file_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q: _stdlib_queue.SimpleQueue = _stdlib_queue.SimpleQueue()

    def cmd_start(self, folder: Path, _fmt: TraceFormat, base_name: str):
        self._q.put(("start", folder, base_name))

    def cmd_write(self, entry: TraceEntry):
        self._q.put(("write", entry))

    def cmd_stop(self):
        self._q.put(("stop",))

    def cmd_sync(self):
        event = threading.Event()
        self._q.put(("sync", event))
        event.wait(timeout=10.0)

    def cmd_quit(self):
        self._q.put(("quit",))
        self.wait(5000)

    def run(self):
        writer: TraceStoreWriter | None = None

        while True:
            item = self._q.get()
            op = item[0]

            if op == "start":
                _, folder, base_name = item
                if writer is not None:
                    writer.close()
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{base_name}.ctb"
                writer = TraceStoreWriter(path)
                writer.open()
                self.file_changed.emit(str(path))

            elif op == "write":
                if writer is None:
                    continue
                _, entry = item
                writer.append_entry(entry)

            elif op == "stop":
                if writer is not None:
                    ctb_path = writer.path
                    writer.close()
                    writer = None
                    # Export CTB to TRC format
                    trc_path = _export_ctb_to_trc(ctb_path)
                    if trc_path is not None:
                        self.file_changed.emit(str(trc_path))

            elif op == "sync":
                _, event = item
                if writer is not None:
                    writer.flush()
                event.set()

            elif op == "quit":
                if writer is not None:
                    writer.close()
                break


class TraceModel(QAbstractTableModel):
    """Virtualized trace table model backed by ring buffer + trace store."""

    file_changed = Signal(str)
    entries_committed = Signal()
    rate_updated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # RAM live cache: absolute row index -> entry
        self._ring: deque[tuple[int, TraceEntry]] = deque()
        self._ring_map: dict[int, TraceEntry] = {}

        # On-demand page cache for disk-backed rows
        self._page_cache: OrderedDict[int, list[TraceEntry]] = OrderedDict()

        self._staged: deque[TraceEntry] = deque()
        self._pending: list[CanMessage] = []
        self._pending_directions: list[str] = []

        self._recording = False
        self._live_mode = True

        self._msg_number = 0
        self._start_time: float | None = None
        self._total_rows = 0

        self._trace_folder: Path | None = None
        self._trace_format = TraceFormat.TRC
        self._current_file: str = ""
        self._store_reader: TraceStoreReader | None = None

        self._disk_thread = _DiskWriterThread(self)
        self._disk_thread.file_changed.connect(self._on_disk_file_changed)
        self._disk_thread.start()

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(100)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

        self._view_timer = QTimer(self)
        self._view_timer.setInterval(VIEW_UPDATE_INTERVAL_MS)
        self._view_timer.timeout.connect(self._commit_staged)
        self._view_timer.start()

        self._rate_count = 0
        self._rate_window_start = time.monotonic()

    def _on_disk_file_changed(self, path: str):
        self._current_file = path
        self._store_reader = TraceStoreReader(path) if path else None
        self._page_cache.clear()
        self.file_changed.emit(path)

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def live_mode(self) -> bool:
        return self._live_mode

    def set_live_mode(self, enabled: bool):
        self._live_mode = enabled

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

    @property
    def message_count(self) -> int:
        return self._total_rows

    def flush_all(self):
        self._flush()
        self._commit_staged()
        self.flush_disk()

    def flush_disk(self):
        self._disk_thread.cmd_sync()

    def start(self):
        self.beginResetModel()
        self._ring.clear()
        self._ring_map.clear()
        self._page_cache.clear()
        self._staged.clear()
        self._pending.clear()
        self._pending_directions.clear()
        self._msg_number = 0
        self._total_rows = 0
        self._start_time = None
        self._current_file = ""
        self._store_reader = None
        self.endResetModel()
        self.file_changed.emit("")

        self._rate_count = 0
        self._rate_window_start = time.monotonic()
        self.rate_updated.emit(0)

        self._recording = True
        self._live_mode = True
        if self._trace_folder is not None:
            base_name = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            self._disk_thread.cmd_start(self._trace_folder, self._trace_format, base_name)

    def stop(self):
        self._recording = False
        self._flush()
        self._commit_staged()
        self._disk_thread.cmd_stop()

    def shutdown(self):
        self._batch_timer.stop()
        self._view_timer.stop()
        self._flush()
        self._commit_staged()
        self._disk_thread.cmd_quit()

    def clear(self):
        self.beginResetModel()
        self._ring.clear()
        self._ring_map.clear()
        self._page_cache.clear()
        self._staged.clear()
        self._pending.clear()
        self._pending_directions.clear()
        self._msg_number = 0
        self._total_rows = 0
        self._start_time = None
        self._current_file = ""
        self._store_reader = None
        self.endResetModel()
        self.file_changed.emit("")

    def load_trace_file(self, path: str | Path):
        """Load a pre-existing trace file (CTB format) into the model.

        Clears the current trace and loads the specified CTB file for viewing.
        This is used when opening saved trace files from the project.

        Args:
            path: Path to the .ctb trace file to load.
        """
        path_str = str(path)
        self.clear()
        self._current_file = path_str
        self._store_reader = TraceStoreReader(path_str)

        # Load the row count from the store
        if self._store_reader:
            self._total_rows = self._store_reader.row_count()
            # Populate ring buffer with first rows for display
            if self._total_rows > 0:
                first_rows = self._store_reader.read_range(0, min(RING_BUFFER_SIZE, self._total_rows))
                for entry in first_rows:
                    self._ring.append((len(self._ring), entry))
                    self._ring_map[len(self._ring_map)] = entry
                self.beginResetModel()
                self.endResetModel()

        self._page_cache.clear()
        self.file_changed.emit(path_str)

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

    def _flush(self):
        if not self._pending:
            return

        batch = self._pending
        directions = self._pending_directions
        self._pending = []
        self._pending_directions = []

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
                length=len(msg.data),
                data=msg.data,
            )
            self._disk_thread.cmd_write(entry)
            self._staged.append(entry)

    def _append_ring(self, row_index: int, entry: TraceEntry):
        if len(self._ring) >= RING_BUFFER_SIZE:
            old_row, _ = self._ring.popleft()
            self._ring_map.pop(old_row, None)
        self._ring.append((row_index, entry))
        self._ring_map[row_index] = entry

    def _commit_staged(self):
        if not self._staged:
            return

        staged = list(self._staged)
        self._staged.clear()

        start_row = self._total_rows
        end_row = start_row + len(staged) - 1

        for i, entry in enumerate(staged):
            self._append_ring(start_row + i, entry)

        self.beginInsertRows(QModelIndex(), start_row, end_row)
        self._total_rows += len(staged)
        self.endInsertRows()

        # Last page may have changed in-place due to appends.
        if self._total_rows > 0:
            last_page = (self._total_rows - 1) // PAGE_SIZE
            self._page_cache.pop(last_page, None)

        self.entries_committed.emit()

    def _get_page(self, page_index: int) -> list[TraceEntry]:
        cached = self._page_cache.get(page_index)
        if cached is not None:
            self._page_cache.move_to_end(page_index)
            return cached

        reader = self._store_reader
        if reader is None:
            return []

        start = page_index * PAGE_SIZE
        try:
            page = reader.read_range(start, PAGE_SIZE)
        except (OSError, ValueError):
            return []
        self._page_cache[page_index] = page
        self._page_cache.move_to_end(page_index)

        while len(self._page_cache) > PAGE_CACHE_LIMIT:
            self._page_cache.popitem(last=False)

        return page

    def _get_entry(self, row: int) -> TraceEntry | None:
        cached = self._ring_map.get(row)
        if cached is not None:
            return cached

        page_index = row // PAGE_SIZE
        in_page = row % PAGE_SIZE
        page = self._get_page(page_index)
        if in_page < 0 or in_page >= len(page):
            return None
        return page[in_page]

    def iter_all_entries(self):
        self.flush_all()

        if self._store_reader is not None:
            try:
                yield from self._store_reader.iter_entries()
            except (OSError, ValueError):
                return
            return

        for row, entry in self._ring:
            if row < self._total_rows:
                yield entry

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return self._total_rows

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            if section == 8:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = index.row()
        if row < 0 or row >= self._total_rows:
            return None

        entry = self._get_entry(row)
        if entry is None:
            return None

        match index.column():
            case 0:
                return entry.number
            case 1:
                return f"{entry.timestamp:.3f}"
            case 2:
                return entry.bus
            case 3:
                return f"{entry.can_id:08X}" if entry.is_extended_id else f"{entry.can_id:03X}"
            case 4:
                return entry.direction
            case 5:
                return entry.frame_type
            case 6:
                return entry.dlc
            case 7:
                return entry.length
            case 8:
                return " ".join(f"{b:02X}" for b in entry.data)
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
