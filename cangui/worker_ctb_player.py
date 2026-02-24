"""QThread that replays a CTB trace file at configurable speed with direction filtering.

Reads from TraceStoreReader (O(1) random access) and emits individual CAN frames
based on the configured replay mode (Tx Only, Rx Inject, or All).
Speed factors >= 100 are treated as maximum speed (no inter-frame timing).
Supports pause/resume, loop mode, and target bus override.
"""

import time
from enum import Enum

from PySide6.QtCore import QThread, Signal

from cangui.can_message import CanMessage
from cangui.trace_store import TraceStoreReader


class ReplayMode(Enum):
    """Replay mode selection for CTB trace playback."""

    TX_ONLY = "tx_only"
    RX_ONLY = "rx_only"
    ALL = "all"


class CtbTracePlayer(QThread):
    """Replays a CTB trace file with configurable mode, speed, and bus override.

    The player reads from TraceStoreReader and emits CanMessage frames through
    tx_frame or rx_frame signals based on the configured replay mode. Timing is
    preserved by sleeping between frames, scaled by the speed multiplier.

    Signals:
        tx_frame: Emitted with CanMessage for each Tx record (when mode includes Tx).
        rx_frame: Emitted with (CanMessage, direction_str) for each Rx record
            (when mode includes Rx).
        progress_changed: Emitted with current timestamp (seconds) for UI updates.
        finished_playback: Emitted when playback completes or is stopped.

    Args:
        reader: A TraceStoreReader instance with loaded CTB data.
        parent: Optional Qt parent object.
    """

    tx_frame = Signal(CanMessage)
    rx_frame = Signal(CanMessage, str)  # (msg, "Rx")
    progress_changed = Signal(float)  # current timestamp in seconds
    finished_playback = Signal()

    def __init__(self, reader: TraceStoreReader, parent=None):
        """Initialize the CTB trace player."""
        super().__init__(parent)
        self._reader = reader
        self._speed = 1.0
        self._mode = ReplayMode.ALL
        self._target_bus = 0  # 0 = use original bus from record
        self._loop = False
        self._running = False
        self._paused = False

    @property
    def speed(self) -> float:
        """Playback speed multiplier (0.1 – 100+).

        Values >= 100 are treated as "maximum speed" (no inter-frame sleep).
        Values < 0.1 are clamped to 0.1.
        """
        return self._speed

    @speed.setter
    def speed(self, value: float):
        """Set the playback speed multiplier, clamped to a minimum of 0.1."""
        self._speed = max(0.1, value)

    @property
    def mode(self) -> ReplayMode:
        """Current replay mode (Tx Only, Rx Only, or All)."""
        return self._mode

    @mode.setter
    def mode(self, value: ReplayMode):
        """Set the replay mode."""
        self._mode = value

    @property
    def target_bus(self) -> int:
        """Target CAN bus number (0 = use original bus from record)."""
        return self._target_bus

    @target_bus.setter
    def target_bus(self, value: int):
        """Set the target bus number; 0 means use original bus."""
        self._target_bus = max(0, min(16, value))

    @property
    def loop(self) -> bool:
        """Whether to loop playback at the end."""
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        """Enable or disable loop mode."""
        self._loop = value

    def pause(self):
        """Pause playback; the thread remains alive but sleeps until resumed."""
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

        Iterates over TraceStoreReader entries, converting each to a CanMessage,
        filtering by replay mode, and emitting either tx_frame or rx_frame.
        Timing is preserved with 10 ms sleep increments for responsiveness.

        Loop mode restarts from the beginning when enabled.
        """
        self._running = True
        self._paused = False

        while True:  # Outer loop for loop mode
            wall_start = time.monotonic()
            trace_start = None

            for entry in self._reader.iter_entries():
                if not self._running:
                    break
                while self._paused and self._running:
                    time.sleep(0.01)
                if not self._running:
                    break

                # Initialize trace_start on first entry
                if trace_start is None:
                    trace_start = entry.timestamp

                # Compute timing offset from first entry
                trace_elapsed = entry.timestamp - trace_start

                # Sleep to maintain timing (unless speed >= 100 for max speed)
                if 0 < self._speed < 100:
                    target_wall = wall_start + (trace_elapsed / self._speed)
                    now = time.monotonic()
                    wait = target_wall - now
                    if wait > 0:
                        # Sleep in small increments to stay responsive to stop
                        while wait > 0 and self._running and not self._paused:
                            time.sleep(min(wait, 0.01))
                            wait = target_wall - time.monotonic()

                # Convert TraceEntry to CanMessage
                msg = CanMessage(
                    arbitration_id=entry.can_id,
                    data=entry.data[: entry.length],  # Trim zero-padding
                    is_extended_id=entry.is_extended_id,
                    is_fd=entry.frame_type == "FD",
                    is_remote_frame=entry.frame_type == "RTR",
                    is_error_frame=entry.frame_type == "Error",
                    dlc=entry.dlc,
                    timestamp=entry.timestamp,
                    bus=self._target_bus if self._target_bus != 0 else entry.bus,
                )

                # Emit based on replay mode and direction
                if entry.direction == "Tx" and self._mode in (
                    ReplayMode.TX_ONLY,
                    ReplayMode.ALL,
                ):
                    self.tx_frame.emit(msg)
                elif entry.direction == "Rx" and self._mode in (
                    ReplayMode.RX_ONLY,
                    ReplayMode.ALL,
                ):
                    self.rx_frame.emit(msg, "Rx")

                # Emit progress
                self.progress_changed.emit(entry.timestamp)

            # Check if we should loop
            if not self._loop or not self._running:
                break

            # Reset for next loop iteration
            wall_start = time.monotonic()

        self.finished_playback.emit()
