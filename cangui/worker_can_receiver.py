"""Background QThread that receives CAN frames without blocking the UI.

The receiver accumulates frames in a local Python list and emits them as a
batch every :data:`_BATCH_INTERVAL` seconds, or immediately when the batch
reaches :data:`_BATCH_MAX` frames.  This decouples the ~1 kHz CAN bus from
the ~60 fps UI refresh rate.

See :doc:`/architecture/threading` for the full batching strategy.
"""

import time

from PySide6.QtCore import QThread, Signal

from cangui.can_bus import CanBus
from cangui.can_message import CanMessage

# Emit a batch every BATCH_INTERVAL seconds or when BATCH_MAX messages accumulate
_BATCH_INTERVAL = 0.020  # 20 ms
_BATCH_MAX = 500


class CanReceiver(QThread):
    """Background thread that polls a :class:`~cangui.can_bus.CanBus` for frames.

    One ``CanReceiver`` is created per active CAN connection by
    :class:`~cangui.service_can.CanService`.  It runs until :meth:`stop` is
    called (e.g., on connection teardown or application exit).

    Signals:
        message_received: Emitted with a non-empty batch of frames.  Connected
            to :meth:`~cangui.service_message_dispatcher.MessageDispatcher.dispatch_batch`
            via a Qt queued connection (automatic cross-thread delivery).

    Args:
        bus: The connected :class:`~cangui.can_bus.CanBus` to poll.
        parent: Optional Qt parent object.
    """

    message_received = Signal(list)  # list[CanMessage]

    def __init__(self, bus: CanBus, parent=None):
        """Bind the receiver to *bus*; does not start the receive loop."""
        super().__init__(parent)
        self._bus = bus
        self._running = False

    def run(self):
        """Main receive loop — runs on the worker thread.

        Calls ``CanBus.recv(timeout=10 ms)`` in a tight loop.  When either
        the batch interval (:data:`_BATCH_INTERVAL`) has elapsed or the batch
        has grown to :data:`_BATCH_MAX` frames, emits
        :attr:`message_received` and resets the batch.

        Any frames accumulated at the time :attr:`_running` becomes ``False``
        are flushed as a final emission before the thread exits.

        Note:
            This method must not call any Qt UI functions.  The only
            cross-thread communication is via the ``message_received`` signal.
        """
        self._running = True
        batch: list[CanMessage] = []
        last_emit = time.monotonic()

        while self._running:
            msg = self._bus.recv(timeout=0.010)
            if msg is not None:
                batch.append(msg)

            now = time.monotonic()
            if batch and (now - last_emit >= _BATCH_INTERVAL or len(batch) >= _BATCH_MAX):
                self.message_received.emit(batch)
                batch = []
                last_emit = now

        # Flush remaining
        if batch:
            self.message_received.emit(batch)

    def stop(self):
        """Request the receive loop to stop and wait up to 2 seconds.

        Sets :attr:`_running` to ``False`` so the next recv timeout causes
        the loop to exit.  Blocks the calling thread (usually the main thread
        during application teardown) for up to 2000 ms.
        """
        self._running = False
        self.wait(2000)
