from PySide6.QtCore import QObject, Signal

from cangui.can_message import CanMessage


class MessageDispatcher(QObject):
    message_received = Signal(CanMessage)
    messages_received = Signal(list)  # list[CanMessage]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rx_hook = None  # Callable[[int, bytes], bytes | None] | None

    def set_rx_hook(self, hook):
        """Set a function applied to each received frame before dispatch.

        hook(arb_id: int, data: bytes) -> bytes | None
            Return modified data, or None to drop the frame.
        Pass None to clear the hook.
        """
        self._rx_hook = hook

    def dispatch(self, msg: CanMessage):
        """Dispatch a single message (used by trace player)."""
        if self._rx_hook is not None:
            result = self._rx_hook(msg.arbitration_id, msg.data)
            if result is None:
                return
            if result != msg.data:
                msg.data = result
        self.message_received.emit(msg)

    def dispatch_batch(self, messages: list):
        """Dispatch a batch of messages (used by CAN receiver)."""
        if self._rx_hook is not None:
            processed = []
            for msg in messages:
                result = self._rx_hook(msg.arbitration_id, msg.data)
                if result is not None:
                    if result != msg.data:
                        msg.data = result
                    processed.append(msg)
            messages = processed
        self.messages_received.emit(messages)
