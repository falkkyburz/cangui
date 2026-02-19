import bisect

import numpy as np
from PySide6.QtCore import QObject, QTimer

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder


MAX_DISPLAY_POINTS = 5000


def lttb_downsample(x: np.ndarray, y: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Largest Triangle Three Buckets downsampling.

    Reduces data to n points while preserving visual shape (peaks, valleys).
    """
    length = len(x)
    if length <= n or n < 3:
        return x, y

    out_x = np.empty(n, dtype=np.float64)
    out_y = np.empty(n, dtype=np.float64)

    # Always keep first and last
    out_x[0] = x[0]
    out_y[0] = y[0]
    out_x[n - 1] = x[-1]
    out_y[n - 1] = y[-1]

    bucket_size = (length - 2) / (n - 2)

    a_idx = 0
    for i in range(1, n - 1):
        # Calculate bucket range
        start = int((i - 1) * bucket_size) + 1
        end = int(i * bucket_size) + 1
        end = min(end, length)

        # Next bucket average (for triangle area calculation)
        next_start = int(i * bucket_size) + 1
        next_end = int((i + 1) * bucket_size) + 1
        next_end = min(next_end, length)

        avg_x = np.mean(x[next_start:next_end])
        avg_y = np.mean(y[next_start:next_end])

        # Find point with max triangle area
        max_area = -1.0
        max_idx = start
        ax = x[a_idx]
        ay = y[a_idx]

        for j in range(start, end):
            area = abs((ax - avg_x) * (y[j] - ay) - (ax - x[j]) * (avg_y - ay))
            if area > max_area:
                max_area = area
                max_idx = j

        out_x[i] = x[max_idx]
        out_y[i] = y[max_idx]
        a_idx = max_idx

    return out_x, out_y


class SignalBuffer:
    """Stores all raw samples and produces display-ready downsampled views.

    Raw data is kept indefinitely (like the trace log).  Display data is a
    rolling time-window slice, downsampled with LTTB, and only recomputed
    when new samples have arrived since the last render call.
    """

    def __init__(self, arb_id: int, signal_name: str, unit: str = ""):
        self.arb_id = arb_id
        self.signal_name = signal_name
        self.unit = unit
        # All samples, never trimmed — O(1) appends via Python list
        self._times: list[float] = []
        self._values: list[float] = []
        # Set whenever new data arrives; cleared by consume_display_data()
        self._dirty = False

    def append(self, t: float, value: float):
        self._times.append(t)
        self._values.append(value)
        self._dirty = True

    def consume_display_data(
        self, time_window: float, max_points: int
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return display arrays only if new data arrived since the last call.

        Returns None when nothing has changed so the caller can skip setData().
        The arrays cover the rolling *time_window* and contain at most
        *max_points* points (LTTB-downsampled).  All raw samples are retained.
        """
        if not self._dirty:
            return None
        self._dirty = False

        if not self._times:
            return np.empty(0, np.float64), np.empty(0, np.float64)

        t_end = self._times[-1]
        start_idx = bisect.bisect_left(self._times, t_end - time_window)

        times = np.array(self._times[start_idx:], dtype=np.float64)
        values = np.array(self._values[start_idx:], dtype=np.float64)

        if len(times) > max_points:
            times, values = lttb_downsample(times, values, max_points)

        return times, values

    def mark_dirty(self):
        """Force a display refresh on the next consume_display_data() call."""
        self._dirty = True

    def clear(self):
        self._times.clear()
        self._values.clear()
        self._dirty = True  # Trigger a blank redraw after clear


class PlotDataService(QObject):
    """Manages rolling time-series buffers for plotted signals."""

    def __init__(self, decoder: SignalDecoder, parent=None):
        super().__init__(parent)
        self._decoder = decoder
        self._buffers: dict[tuple[int, str], SignalBuffer] = {}
        self._time_window = 10.0  # seconds
        self._max_display_points = MAX_DISPLAY_POINTS
        self._start_time: float | None = None
        self._pending: list[CanMessage] = []

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(50)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    @property
    def time_window(self) -> float:
        return self._time_window

    @time_window.setter
    def time_window(self, value: float):
        self._time_window = max(1.0, value)
        # New window width means display slices must be recomputed
        for buf in self._buffers.values():
            buf.mark_dirty()

    @property
    def max_display_points(self) -> int:
        return self._max_display_points

    @max_display_points.setter
    def max_display_points(self, value: int):
        self._max_display_points = max(100, value)
        for buf in self._buffers.values():
            buf.mark_dirty()

    @property
    def buffers(self) -> dict[tuple[int, str], SignalBuffer]:
        return self._buffers

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        key = (arb_id, signal_name)
        if key not in self._buffers:
            self._buffers[key] = SignalBuffer(arb_id=arb_id, signal_name=signal_name, unit=unit)

    def remove_signal(self, arb_id: int, signal_name: str):
        self._buffers.pop((arb_id, signal_name), None)

    def has_signal(self, arb_id: int, signal_name: str) -> bool:
        return (arb_id, signal_name) in self._buffers

    def consume_display_data(
        self, key: tuple[int, str]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return new display data for *key* if available since the last call, else None."""
        buf = self._buffers.get(key)
        if buf is None:
            return None
        return buf.consume_display_data(self._time_window, self._max_display_points)

    def on_message(self, msg: CanMessage):
        """Queue a single message for processing."""
        if any(k[0] == msg.arbitration_id for k in self._buffers):
            self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Queue a batch of messages for processing."""
        watched_ids = {k[0] for k in self._buffers}
        if not watched_ids:
            return
        for msg in messages:
            if msg.arbitration_id in watched_ids:
                self._pending.append(msg)

    def _flush(self):
        """Process pending messages and update signal buffers."""
        if not self._pending:
            return
        batch = self._pending
        self._pending = []

        for msg in batch:
            decoded = self._decoder.decode(msg.arbitration_id, msg.data)
            if not decoded:
                continue

            if self._start_time is None:
                self._start_time = msg.timestamp

            t = msg.timestamp - self._start_time

            for ds in decoded:
                key = (msg.arbitration_id, ds.name)
                buf = self._buffers.get(key)
                if buf is None:
                    continue
                try:
                    value = float(ds.value)
                except (TypeError, ValueError):
                    continue
                buf.append(t, value)

    def clear(self):
        for buf in self._buffers.values():
            buf.clear()
        self._pending.clear()
        self._start_time = None

    @property
    def signal_list(self) -> list[tuple[int, str, str]]:
        """Return list of (arb_id, signal_name, unit) for all watched signals."""
        return [(b.arb_id, b.signal_name, b.unit) for b in self._buffers.values()]
