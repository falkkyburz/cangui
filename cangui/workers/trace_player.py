import time

from PySide6.QtCore import QThread, Signal

from cangui.core.can_message import CanMessage
from cangui.core.trace_reader import TraceReader


class TracePlayer(QThread):
    """Replays TRC files at configurable speed, emitting messages with timing."""

    message_played = Signal(CanMessage, str)  # message, direction
    progress_changed = Signal(float)  # current time offset
    finished_playback = Signal()

    def __init__(self, reader: TraceReader, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._speed = 1.0
        self._running = False
        self._paused = False

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float):
        self._speed = max(0.1, value)

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._running = False
        self._paused = False
        self.wait(2000)

    def run(self):
        entries = self._reader.entries
        if not entries:
            self.finished_playback.emit()
            return

        self._running = True
        self._paused = False
        wall_start = time.monotonic()
        trace_start = entries[0].time_offset

        for entry in entries:
            if not self._running:
                break
            while self._paused and self._running:
                time.sleep(0.01)
            if not self._running:
                break

            trace_elapsed = entry.time_offset - trace_start
            if self._speed > 0 and self._speed < 100:
                target_wall = wall_start + trace_elapsed / self._speed
                now = time.monotonic()
                wait = target_wall - now
                if wait > 0:
                    # Sleep in small increments to stay responsive to stop
                    while wait > 0 and self._running and not self._paused:
                        time.sleep(min(wait, 0.01))
                        wait = target_wall - time.monotonic()

            self.message_played.emit(entry.message, entry.direction)
            self.progress_changed.emit(entry.time_offset)

        self.finished_playback.emit()
