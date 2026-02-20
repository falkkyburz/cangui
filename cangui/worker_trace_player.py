"""QThread that replays a loaded trace file at configurable speed.

Emits each :class:`~cangui.can_message.CanMessage` individually (single-frame
path) so the trace view and the RX model both update in real time during
playback.  Speed factors above 100× are treated as "maximum speed" (no sleep).
"""

import time

from PySide6.QtCore import QThread, Signal

from cangui.can_message import CanMessage
from cangui.trace_reader import TraceReader


class TracePlayer(QThread):
    """Replays a :class:`~cangui.trace_reader.TraceReader` entry list in real time.

    The player reads timestamps from the loaded trace and sleeps between
    frames to maintain the original timing, scaled by :attr:`speed`.
    Pause/resume is implemented with a busy-wait polling loop (10 ms ticks)
    so the thread remains responsive to :meth:`stop` calls.

    Signals:
        message_played: Emitted once per trace entry with the reconstructed
            :class:`~cangui.can_message.CanMessage` and the original direction
            string (``"Rx"`` or ``"Tx"``).
        progress_changed: Emitted with the current trace time offset in
            seconds, suitable for driving a progress indicator.
        finished_playback: Emitted when the last entry has been played or
            after :meth:`stop` is called.

    Args:
        reader: A :class:`~cangui.trace_reader.TraceReader` whose
            :attr:`~cangui.trace_reader.TraceReader.entries` have been loaded.
        parent: Optional Qt parent object.
    """

    message_played = Signal(CanMessage, str)  # message, direction
    progress_changed = Signal(float)  # current time offset in seconds
    finished_playback = Signal()

    def __init__(self, reader: TraceReader, parent=None):
        """Initialise the player; call :meth:`start` (via ``QThread.start()``) to begin playback."""
        super().__init__(parent)
        self._reader = reader
        self._speed = 1.0
        self._running = False
        self._paused = False

    @property
    def speed(self) -> float:
        """Playback speed multiplier (0.1 – 100+).

        Values ≥ 100 are treated as "maximum speed" (no inter-frame sleep).
        Values < 0.1 are clamped to 0.1.
        """
        return self._speed

    @speed.setter
    def speed(self, value: float):
        """Set the playback speed multiplier, clamped to a minimum of 0.1."""
        self._speed = max(0.1, value)

    def pause(self):
        """Pause playback.  The thread remains alive but sleeps until resumed."""
        self._paused = True

    def resume(self):
        """Resume playback from the current position."""
        self._paused = False

    def stop(self):
        """Abort playback, clear the paused flag, and wait up to 2 seconds."""
        self._running = False
        self._paused = False
        self.wait(2000)

    def run(self):
        """Playback loop — runs on the worker thread.

        Iterates over :attr:`TraceReader.entries`, computing the wall-clock
        target time for each frame from its trace timestamp and the configured
        :attr:`speed`.  Sleeps in 10 ms increments between frames so
        :meth:`stop`/:meth:`pause` are honoured promptly.

        Emits :attr:`finished_playback` unconditionally when the loop ends
        (normal completion or stop request).
        """
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
