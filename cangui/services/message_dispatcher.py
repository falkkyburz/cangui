from PySide6.QtCore import QObject, Signal

from cangui.core.can_message import CanMessage


class MessageDispatcher(QObject):
    message_received = Signal(CanMessage)
    messages_received = Signal(list)  # list[CanMessage]

    def dispatch(self, msg: CanMessage):
        """Dispatch a single message (used by trace player)."""
        self.message_received.emit(msg)

    def dispatch_batch(self, messages: list):
        """Dispatch a batch of messages (used by CAN receiver)."""
        self.messages_received.emit(messages)
