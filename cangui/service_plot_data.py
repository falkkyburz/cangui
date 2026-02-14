from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

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


@dataclass
class SignalBuffer:
    arb_id: int
    signal_name: str
    unit: str
    times: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    values: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))

    def append(self, t: float, value: float):
        self.times = np.append(self.times, t)
        self.values = np.append(self.values, value)

    def trim(self, max_age: float):
        """Remove samples older than max_age seconds from the latest."""
        if len(self.times) == 0:
            return
        cutoff = self.times[-1] - max_age
        mask = self.times >= cutoff
        self.times = self.times[mask]
        self.values = self.values[mask]

    def clear(self):
        self.times = np.empty(0, dtype=np.float64)
        self.values = np.empty(0, dtype=np.float64)


class PlotDataService(QObject):
    """Manages rolling time-series buffers for plotted signals."""

    data_updated = Signal()

    def __init__(self, decoder: SignalDecoder, parent=None):
        super().__init__(parent)
        self._decoder = decoder
        self._buffers: dict[tuple[int, str], SignalBuffer] = {}
        self._time_window = 10.0  # seconds
        self._max_display_points = MAX_DISPLAY_POINTS
        self._start_time: float | None = None
        self._pending: list[CanMessage] = []

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(100)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    @property
    def time_window(self) -> float:
        return self._time_window

    @time_window.setter
    def time_window(self, value: float):
        self._time_window = max(1.0, value)

    @property
    def max_display_points(self) -> int:
        return self._max_display_points

    @max_display_points.setter
    def max_display_points(self, value: int):
        self._max_display_points = max(100, value)

    @property
    def buffers(self) -> dict[tuple[int, str], SignalBuffer]:
        return self._buffers

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        key = (arb_id, signal_name)
        if key not in self._buffers:
            self._buffers[key] = SignalBuffer(arb_id=arb_id, signal_name=signal_name, unit=unit)

    def remove_signal(self, arb_id: int, signal_name: str):
        key = (arb_id, signal_name)
        self._buffers.pop(key, None)

    def has_signal(self, arb_id: int, signal_name: str) -> bool:
        return (arb_id, signal_name) in self._buffers

    def get_display_data(self, key: tuple[int, str]) -> tuple[np.ndarray, np.ndarray] | None:
        """Return downsampled data suitable for display."""
        buf = self._buffers.get(key)
        if buf is None or len(buf.times) == 0:
            return None
        if len(buf.times) <= self._max_display_points:
            return buf.times, buf.values
        return lttb_downsample(buf.times, buf.values, self._max_display_points)

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

        # Trim all active buffers once per flush
        for buf in self._buffers.values():
            if len(buf.times) > 0:
                buf.trim(self._time_window)

        self.data_updated.emit()

    def clear(self):
        for buf in self._buffers.values():
            buf.clear()
        self._pending.clear()
        self._start_time = None

    @property
    def signal_list(self) -> list[tuple[int, str, str]]:
        """Return list of (arb_id, signal_name, unit) for all watched signals."""
        return [(b.arb_id, b.signal_name, b.unit) for b in self._buffers.values()]
