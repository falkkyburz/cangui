import time

from PySide6.QtCore import QThread, Signal

from cangui.core.can_bus import CanBus
from cangui.core.can_message import CanMessage

# Emit a batch every BATCH_INTERVAL seconds or when BATCH_MAX messages accumulate
_BATCH_INTERVAL = 0.020  # 20 ms
_BATCH_MAX = 500


class CanReceiver(QThread):
    message_received = Signal(list)  # list[CanMessage]

    def __init__(self, bus: CanBus, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._running = False

    def run(self):
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
        self._running = False
        self.wait(2000)
