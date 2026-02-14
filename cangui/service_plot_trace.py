from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from cangui.can_message import CanMessage
from cangui.trace_writer import TraceWriter, TraceFormat, create_trace_writer


MAX_FILE_SIZE = 1_000_000_000  # 1 GB


class PlotTraceService(QObject):
    """Writes filtered trace files containing only messages for plotted signals."""

    file_changed = Signal(str)  # emitted when a new trace file is opened
    recording_changed = Signal(bool)

    def __init__(self, parent=None):
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
        return self._recording

    def set_trace_folder(self, folder: Path | None):
        self._trace_folder = folder

    def set_trace_format(self, fmt: str):
        try:
            self._trace_format = TraceFormat(fmt)
        except ValueError:
            self._trace_format = TraceFormat.TRC

    def set_watched_arb_ids(self, arb_ids: set[int]):
        self._watched_arb_ids = arb_ids

    def add_arb_id(self, arb_id: int):
        self._watched_arb_ids.add(arb_id)

    def remove_arb_id(self, arb_id: int):
        self._watched_arb_ids.discard(arb_id)

    def start(self):
        if self._recording:
            return
        self._recording = True
        self._open_file()
        self.recording_changed.emit(True)

    def stop(self):
        if not self._recording:
            return
        self._recording = False
        self._close_file()
        self.recording_changed.emit(False)

    def on_message(self, msg: CanMessage):
        if not self._recording:
            return
        if msg.arbitration_id not in self._watched_arb_ids:
            return
        self._write(msg)

    def on_messages(self, messages: list[CanMessage]):
        if not self._recording or not self._watched_arb_ids:
            return
        for msg in messages:
            if msg.arbitration_id in self._watched_arb_ids:
                self._write(msg)

    def _write(self, msg: CanMessage):
        if self._writer is None:
            return
        if self._writer.file_size >= MAX_FILE_SIZE:
            self._roll_file()
        self._writer.write(msg, direction="Rx")

    def _open_file(self):
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
        if self._writer is not None:
            self._writer.close()
        self._file_index += 1
        ext = self._trace_format.value
        path = self._trace_folder / f"{self._base_name}_{self._file_index:03d}.{ext}"
        self._writer = create_trace_writer(path, self._trace_format)
        self._writer.open()
        self.file_changed.emit(str(path))

    def _close_file(self):
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self.file_changed.emit("")

    @property
    def current_file(self) -> str:
        if self._writer is not None:
            return str(self._writer.path)
        return ""
