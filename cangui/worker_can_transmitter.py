"""Background QThread that drives cyclic CAN TX messages.

Uses the *snapshot pattern* to safely read the TX model from a worker thread
without locking.  See :doc:`/architecture/threading` for details.
"""

import time
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from cangui.can_message import CanMessage
from cangui.model_tx_message import TxMessageModel

# Emit accumulated send counts every COUNTER_INTERVAL seconds
_COUNTER_INTERVAL = 0.200  # 200 ms


@dataclass(frozen=True)
class _TxSnapshot:
    """Immutable snapshot of one TX row, safe to read from the worker thread.

    Built on the main thread by :meth:`CanTransmitter._build_snapshot` and
    atomically swapped into :attr:`CanTransmitter._snapshot`.  Because the
    dataclass is frozen and all fields are primitive types (``int``, ``bool``,
    ``bytes``), the worker thread can read it without any synchronization
    beyond CPython's GIL-protected reference swap.

    Attributes:
        row: Row index in :class:`~cangui.model_tx_message.TxMessageModel`,
            used to route ``counts_updated`` deltas back to the correct cell.
        can_id: Arbitration ID for the frame.
        raw_data: Payload bytes at the time the snapshot was taken.
        is_extended_id: ``True`` for 29-bit extended IDs.
        length: DLC value.
        bus: Logical bus number for routing to the correct connection.
        cycle_time_ms: Transmission interval in milliseconds.
        cycle_enabled: ``True`` if the row's cyclic transmission is active.
    """

    row: int
    can_id: int
    raw_data: bytes
    is_extended_id: bool
    length: int
    bus: int
    cycle_time_ms: int
    cycle_enabled: bool


class CanTransmitter(QThread):
    """Periodically transmits enabled TX messages on a background thread.

    Reads the TX message list via an immutable :class:`_TxSnapshot` rebuilt
    on the main thread whenever the model changes.  This eliminates the need
    for mutex locks while still ensuring the worker always uses consistent data.

    Signals:
        counts_updated: Emitted every 200 ms with a ``{row: count_delta}``
            dict so the model can update the send-count column without the
            worker touching Qt model internals.
        snapshot_requested: Emitted by the worker when the snapshot is stale;
            connected (queued) to :meth:`_build_snapshot` on the main thread.

    Args:
        tx_model: The TX message model to snapshot.
        send_func: Callable ``(CanMessage) -> bool`` that delivers the frame
            to the active CAN bus and returns success.  Typically
            :meth:`~cangui.ui_main_window.MainWindow._send_message`.
        parent: Optional Qt parent object.
    """

    counts_updated = Signal(object)  # {row: count_delta}
    snapshot_requested = Signal()

    def __init__(self, tx_model: TxMessageModel, send_func, parent=None):
        """Bind the transmitter to *tx_model* and a callable *send_func* for frame dispatch."""
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
        """Mark the snapshot as dirty so the worker requests a rebuild.

        Called on the main thread via model signals.
        """
        self._snapshot_stale = True

    def _build_snapshot(self):
        """Rebuild the immutable TX snapshot from the current model state.

        Runs on the **main thread** (connected via Qt's default queued
        connection from :attr:`snapshot_requested`).  Reads
        :attr:`TxMessageModel.items` and converts each item to a frozen
        :class:`_TxSnapshot`.
        """
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
        """Cyclic TX loop — runs on the worker thread.

        Checks per-row timers every 1 ms.  When a row's timer fires,
        constructs a :class:`~cangui.can_message.CanMessage` and calls
        ``send_func``.  Accumulated send counts are emitted every 200 ms.

        Requests a snapshot rebuild (via :attr:`snapshot_requested`) at most
        every 200 ms when the snapshot is marked stale, preventing excessive
        main-thread round-trips on rapid model edits.

        Note:
            ``send_func`` is called from this thread but is expected to
            dispatch the actual ``Bus.send()`` call on the correct thread.
            In practice, ``MainWindow._send_message`` is safe to call from
            any thread because it only reads the connections list (which is
            stable while the transmitter runs) and python-can's ``Bus.send``
            is thread-safe.
        """
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
                        row=item.row,
                    )
                    if self._send(msg):
                        counts[item.row] = counts.get(item.row, 0) + 1
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
        """Stop the transmit loop and wait up to 2 seconds for the thread to exit."""
        self._running = False
        self.wait(2000)
