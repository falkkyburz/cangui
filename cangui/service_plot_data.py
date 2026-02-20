"""Plot data service for buffering and downsampling decoded CAN signal samples.

This module provides the PlotDataService and SignalBuffer classes that together
manage rolling time-series data for live signal plots in cangui.  Incoming CAN
messages are queued and flushed every 50 ms by a QTimer so that the UI thread is
never blocked by decoding work.  Before passing samples to pyqtgraph, the buffer
applies LTTB (Largest Triangle Three Buckets) downsampling to keep the number of
rendered points bounded at MAX_DISPLAY_POINTS regardless of how long the time
window is or how fast messages arrive.
"""

import bisect

import numpy as np
from PySide6.QtCore import QObject, QTimer

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder


MAX_DISPLAY_POINTS = 5000
"""Default upper bound on the number of points passed to pyqtgraph per render cycle."""


def lttb_downsample(x: np.ndarray, y: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a time-series to *n* points using the Largest Triangle Three Buckets algorithm.

    LTTB iterates over equal-width buckets and, within each bucket, keeps the
    single point that forms the largest triangle area with the previously kept
    point and the centroid of the next bucket.  This strategy preserves visually
    salient features (peaks, troughs, sharp edges) far better than uniform
    sub-sampling.

    The first and last points of the original series are always preserved so
    that the visible time range on screen is not affected by downsampling.

    Args:
        x: Monotonically increasing timestamp array (seconds since recording
            start).  Must have the same length as *y*.
        y: Signal value array corresponding to each timestamp in *x*.
        n: Target number of output points.  If ``len(x) <= n`` or ``n < 3``
            the arrays are returned unchanged.

    Returns:
        A ``(out_x, out_y)`` tuple of float64 arrays, each of length *n*,
        containing the downsampled timestamps and values.  When no downsampling
        is needed the original arrays are returned directly (no copy).

    Note:
        The inner loop is pure Python; for very large ``n`` values consider
        switching to a compiled implementation.  In practice ``n`` is capped at
        MAX_DISPLAY_POINTS (5000) so this is rarely a bottleneck.
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
    """Rolling time-window buffer for a single decoded CAN signal.

    Internally stores parallel Python lists of timestamps and floating-point
    values.  Every 50 ms PlotDataService calls ``trim()`` to evict samples
    that have fallen outside the configured time window, keeping memory usage
    bounded.

    The ``_dirty`` flag tracks whether any new samples have been appended since
    the last call to ``consume_display_data()``.  Callers can skip expensive
    ``setData()`` calls on the plot widget whenever this method returns ``None``.

    Attributes:
        arb_id: CAN arbitration ID of the message that carries this signal.
        signal_name: Name of the decoded signal as it appears in the DBC/ODX
            database (e.g. ``"EngineSpeed"``).
        unit: Physical unit string (e.g. ``"rpm"``), empty when not available.
    """

    def __init__(self, arb_id: int, signal_name: str, unit: str = ""):
        """Initialise an empty buffer for the given signal.

        Args:
            arb_id: CAN arbitration ID of the message that carries this signal.
            signal_name: Decoded signal name as it appears in the database.
            unit: Optional physical unit string (e.g. ``"rpm"``).
        """
        self.arb_id = arb_id
        self.signal_name = signal_name
        self.unit = unit
        self._times: list[float] = []
        self._values: list[float] = []
        self._dirty = False

    def append(self, t: float, value: float):
        """Append a new sample and mark the buffer as dirty.

        Args:
            t: Elapsed time in seconds since the recording started.  Must be
                non-decreasing across successive calls for ``trim()`` and
                ``lttb_downsample()`` to work correctly.
            value: Decoded physical value for the signal at time *t*.
        """
        self._times.append(t)
        self._values.append(value)
        self._dirty = True

    def trim(self, time_window: float):
        """Drop samples older than *time_window* seconds from the front of the buffer.

        Uses ``bisect_left`` for an O(log n) search of the cutoff index, then
        performs a single O(k) slice deletion to evict stale samples.

        Args:
            time_window: Width of the rolling window in seconds.  Samples with
                timestamps earlier than ``latest_time - time_window`` are
                removed.  Must be positive.
        """
        if not self._times:
            return
        cutoff = self._times[-1] - time_window
        idx = bisect.bisect_left(self._times, cutoff)
        if idx > 0:
            del self._times[:idx]
            del self._values[:idx]

    def consume_display_data(
        self, time_window: float, max_points: int
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return downsampled display arrays only when new data has arrived.

        This is the primary read interface for the plot widget.  The caller
        should invoke it every render cycle and skip ``setData()`` entirely
        when ``None`` is returned, avoiding unnecessary redraws.

        ``trim()`` is assumed to have already been called by PlotDataService
        so this method only converts the lists to numpy arrays and applies
        LTTB downsampling when the sample count exceeds *max_points*.

        Args:
            time_window: Rolling window width in seconds (passed through to
                context but not used directly here; trimming is done
                separately by ``trim()``).
            max_points: Maximum number of points to return.  When the buffer
                contains more samples than this, LTTB downsampling is applied.

        Returns:
            A ``(times, values)`` tuple of float64 numpy arrays ready for
            ``PlotDataItem.setData()``, or ``None`` if no new samples have
            arrived since the previous call.  An empty buffer returns two
            zero-length arrays (not ``None``) so the plot is cleared.
        """
        if not self._dirty:
            return None
        self._dirty = False

        if not self._times:
            return np.empty(0, np.float64), np.empty(0, np.float64)

        times = np.array(self._times, dtype=np.float64)
        values = np.array(self._values, dtype=np.float64)

        if len(times) > max_points:
            times, values = lttb_downsample(times, values, max_points)

        return times, values

    def mark_dirty(self):
        """Force a display refresh on the next ``consume_display_data()`` call.

        Use this when a rendering parameter (time window, max points) changes
        but no new samples have arrived, so the plot still needs to be redrawn
        with the updated settings.
        """
        self._dirty = True

    def clear(self):
        """Discard all buffered samples and schedule a blank redraw.

        Sets the dirty flag so that ``consume_display_data()`` returns empty
        arrays on the next call, which causes the plot widget to clear itself.
        """
        self._times.clear()
        self._values.clear()
        self._dirty = True  # Trigger a blank redraw after clear


class PlotDataService(QObject):
    """Manages rolling time-series buffers for all signals currently shown in plots.

    PlotDataService sits between the CAN receiver and the pyqtgraph plot widgets.
    It decodes raw CAN frames, routes signal samples into per-signal
    ``SignalBuffer`` instances, and trims old data to keep memory bounded.

    A 50 ms ``QTimer`` drives the ``_flush()`` method which drains the pending
    message queue, decodes frames, and trims each buffer.  Plot widgets poll
    ``consume_display_data()`` independently (typically in their own refresh
    timer) and receive pre-downsampled numpy arrays only when new data is
    available.

    Threading model:
        ``on_message()`` / ``on_messages()`` are intended to be called from the
        GUI thread (connected to a Qt signal emitted by the CAN worker via a
        queued connection).  ``_flush()`` fires on the same GUI thread via
        ``QTimer``, so no explicit locking is required.
    """

    def __init__(self, decoder: SignalDecoder, parent=None):
        """Initialise the service and start the 50 ms flush timer.

        Args:
            decoder: ``SignalDecoder`` instance used to decode raw CAN frame
                payloads into named signal values.  Must remain valid for the
                lifetime of this object.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._decoder = decoder
        self._buffers: dict[tuple[int, str], SignalBuffer] = {}
        self._time_window = 10.0  # seconds
        self._max_display_points = MAX_DISPLAY_POINTS
        self._start_time: float | None = None
        self._pending: list[CanMessage] = []
        self._active = False

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(50)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

    @property
    def time_window(self) -> float:
        """Width of the rolling display window in seconds.

        The setter enforces a minimum of 1.0 s and marks all existing buffers
        dirty so the next render cycle recomputes their display slices.
        """
        return self._time_window

    @time_window.setter
    def time_window(self, value: float):
        """Set the rolling window width (seconds), clamped to a minimum of 1.0."""
        self._time_window = max(1.0, value)
        # New window width means display slices must be recomputed
        for buf in self._buffers.values():
            buf.mark_dirty()

    @property
    def max_display_points(self) -> int:
        """Maximum number of points returned by ``consume_display_data()`` per signal.

        The setter enforces a minimum of 100 points and marks all buffers dirty.
        Reducing this value speeds up rendering at the cost of visual fidelity.
        """
        return self._max_display_points

    @max_display_points.setter
    def max_display_points(self, value: int):
        """Set the LTTB point cap, clamped to a minimum of 100."""
        self._max_display_points = max(100, value)
        for buf in self._buffers.values():
            buf.mark_dirty()

    @property
    def buffers(self) -> dict[tuple[int, str], SignalBuffer]:
        """Read-only view of the internal buffer dictionary.

        Keys are ``(arb_id, signal_name)`` tuples.  Callers should not mutate
        this dictionary directly; use ``add_signal()`` / ``remove_signal()``
        instead.
        """
        return self._buffers

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        """Register a signal so that incoming messages are decoded and buffered for it.

        If the signal is already registered this method is a no-op.

        Args:
            arb_id: CAN arbitration ID of the message that carries the signal.
            signal_name: Decoded signal name as it appears in the DBC/ODX
                database (e.g. ``"EngineSpeed"``).
            unit: Optional physical unit string (e.g. ``"rpm"``).
        """
        key = (arb_id, signal_name)
        if key not in self._buffers:
            self._buffers[key] = SignalBuffer(arb_id=arb_id, signal_name=signal_name, unit=unit)

    def start(self):
        """Clear all display buffers, reset the time origin, and begin accepting messages.

        Call this when the user starts a new recording or reconnects to the bus.
        All previously buffered samples are discarded and the elapsed-time origin
        is reset so the first arriving message defines t = 0.

        Emits:
            No Qt signals are emitted directly, but each buffer's ``clear()``
            sets its dirty flag so the next render cycle issues blank redraws.
        """
        self._active = True
        for buf in self._buffers.values():
            buf.clear()
        self._pending.clear()
        self._start_time = None

    def stop(self):
        """Stop accepting new messages so the plot display freezes at its current state.

        Any messages that were queued but not yet flushed are discarded.  Call
        ``start()`` to resume data collection.
        """
        self._active = False
        self._pending.clear()

    def remove_signal(self, arb_id: int, signal_name: str):
        """Unregister a signal and discard its buffer.

        Args:
            arb_id: CAN arbitration ID of the message that carries the signal.
            signal_name: Name of the signal to remove.  If the signal is not
                currently registered, this method is a no-op.
        """
        self._buffers.pop((arb_id, signal_name), None)

    def has_signal(self, arb_id: int, signal_name: str) -> bool:
        """Return whether a signal is currently registered with this service.

        Args:
            arb_id: CAN arbitration ID of the message that carries the signal.
            signal_name: Name of the signal to check.

        Returns:
            ``True`` if the ``(arb_id, signal_name)`` pair has a buffer,
            ``False`` otherwise.
        """
        return (arb_id, signal_name) in self._buffers

    def consume_display_data(
        self, key: tuple[int, str]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return new display data for *key* if available since the last call, else None.

        Delegates to ``SignalBuffer.consume_display_data()`` using the service's
        current ``time_window`` and ``max_display_points`` settings.

        Args:
            key: ``(arb_id, signal_name)`` tuple identifying the desired signal.

        Returns:
            A ``(times, values)`` tuple of float64 numpy arrays suitable for
            passing directly to ``PlotDataItem.setData()``, or ``None`` when no
            new samples have arrived since the last call.  Returns ``None`` also
            when *key* is not registered.
        """
        buf = self._buffers.get(key)
        if buf is None:
            return None
        return buf.consume_display_data(self._time_window, self._max_display_points)

    def on_message(self, msg: CanMessage):
        """Queue a single CAN message for processing on the next flush cycle.

        Messages whose arbitration ID does not match any registered signal are
        dropped immediately to avoid unnecessary memory allocation.  This method
        is safe to call at high message rates because actual decoding is deferred
        to ``_flush()``.

        Args:
            msg: Received CAN message.  Only the ``arbitration_id``,
                ``data``, and ``timestamp`` fields are used.
        """
        if not self._active:
            return
        if any(k[0] == msg.arbitration_id for k in self._buffers):
            self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Queue a batch of CAN messages for processing on the next flush cycle.

        Builds a set of watched arbitration IDs first so that each message
        lookup is O(1) rather than O(signals).

        Args:
            messages: List of received CAN messages to filter and enqueue.
                Messages with arbitration IDs not present in any registered
                signal buffer are discarded immediately.
        """
        if not self._active:
            return
        watched_ids = {k[0] for k in self._buffers}
        if not watched_ids:
            return
        for msg in messages:
            if msg.arbitration_id in watched_ids:
                self._pending.append(msg)

    def _flush(self):
        """Decode all pending messages and append samples to the appropriate buffers.

        Called every 50 ms by the internal ``QTimer``.  Swaps the pending list
        atomically so that messages arriving during decoding are captured in the
        next cycle.  After decoding, trims each buffer to the configured
        ``time_window``.

        Non-numeric decoded values (e.g. named states returned as strings) are
        silently skipped because pyqtgraph requires float data.

        Note:
            Runs entirely on the GUI thread; no locking is needed.
        """
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

        for buf in self._buffers.values():
            if buf._times:
                buf.trim(self._time_window)

    def clear(self):
        """Discard all buffered samples and reset the time origin.

        Unlike ``stop()``, this does not deactivate the service; new messages
        continue to be accepted.  Useful when the user wants to reset the plot
        view without disconnecting from the bus.
        """
        for buf in self._buffers.values():
            buf.clear()
        self._pending.clear()
        self._start_time = None

    @property
    def signal_list(self) -> list[tuple[int, str, str]]:
        """Return metadata for all currently registered signals.

        Returns:
            List of ``(arb_id, signal_name, unit)`` tuples, one per registered
            signal, in insertion order.
        """
        return [(b.arb_id, b.signal_name, b.unit) for b in self._buffers.values()]
