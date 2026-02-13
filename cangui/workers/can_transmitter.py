import time
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from cangui.core.can_message import CanMessage
from cangui.models.tx_message_model import TxMessageModel

# Emit accumulated send counts every COUNTER_INTERVAL seconds
_COUNTER_INTERVAL = 0.200  # 200 ms


@dataclass(frozen=True)
class _TxSnapshot:
    """Immutable snapshot of a TX item for the transmitter thread."""
    row: int
    can_id: int
    raw_data: bytes
    is_extended_id: bool
    length: int
    bus: int
    cycle_time_ms: int
    cycle_enabled: bool


class CanTransmitter(QThread):
    """Periodically transmits enabled TX messages."""

    counts_updated = Signal(object)  # {row: count_delta}
    snapshot_requested = Signal()

    def __init__(self, tx_model: TxMessageModel, send_func, parent=None):
        super().__init__(parent)
        self._model = tx_model
        self._send = send_func
        self._running = False
        self._snapshot: list[_TxSnapshot] = []
        self._snapshot_stale = True

        # Snapshot is built on the main thread via signal, safe to read model
        self.snapshot_requested.connect(self._build_snapshot)
        self._model.dataChanged.connect(self._mark_stale)
        self._model.rowsInserted.connect(self._mark_stale)
        self._model.rowsRemoved.connect(self._mark_stale)

    def _mark_stale(self):
        """Mark snapshot as stale so it gets rebuilt on next check."""
        self._snapshot_stale = True

    def _build_snapshot(self):
        """Build an immutable snapshot of TX items (runs on main thread)."""
        self._snapshot = [
            _TxSnapshot(
                row=row,
                can_id=item.can_id,
                raw_data=bytes(item.raw_data),
                is_extended_id=item.is_extended_id,
                length=item.length,
                bus=item.bus,
                cycle_time_ms=item.cycle_time_ms,
                cycle_enabled=item.cycle_enabled,
            )
            for row, item in enumerate(self._model.items)
        ]
        self._snapshot_stale = False

    def run(self):
        self._running = True
        self.snapshot_requested.emit()
        timers: dict[int, float] = {}
        counts: dict[int, int] = {}
        last_count_emit = time.monotonic()
        last_snapshot_request = 0.0

        while self._running:
            now = time.monotonic()
            snapshot = self._snapshot

            # Request snapshot rebuild at most every 200ms when stale
            if self._snapshot_stale and now - last_snapshot_request >= 0.200:
                self.snapshot_requested.emit()
                last_snapshot_request = now

            for item in snapshot:
                if not item.cycle_enabled:
                    timers.pop(item.row, None)
                    continue

                next_time = timers.get(item.row, 0.0)
                if now >= next_time:
                    msg = CanMessage(
                        arbitration_id=item.can_id,
                        data=item.raw_data,
                        is_extended_id=item.is_extended_id,
                        dlc=item.length,
                        bus=item.bus,
                    )
                    try:
                        self._send(msg)
                        counts[item.row] = counts.get(item.row, 0) + 1
                    except Exception:
                        pass
                    timers[item.row] = now + item.cycle_time_ms / 1000.0

            # Emit accumulated counts periodically
            if counts and now - last_count_emit >= _COUNTER_INTERVAL:
                self.counts_updated.emit(counts)
                counts = {}
                last_count_emit = now

            time.sleep(0.001)

        # Flush remaining counts
        if counts:
            self.counts_updated.emit(counts)

    def stop(self):
        self._running = False
        self.wait(2000)
