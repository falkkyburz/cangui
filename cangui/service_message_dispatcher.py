"""Central fan-out hub for all received CAN frames.

The dispatcher sits between the CAN bus receivers and all consumers (models,
services).  It is the single point where the optional script plugin hook is
applied to incoming frames.

Two signal paths serve two distinct use cases:

* ``messages_received`` — batch path, used by
  :class:`~cangui.worker_can_receiver.CanReceiver` for live bus traffic.
* ``message_received`` — single-frame path, used by
  :class:`~cangui.worker_trace_player.TracePlayer` during replay.
"""

from PySide6.QtCore import QObject, Signal

from cangui.can_message import CanMessage


class MessageDispatcher(QObject):
    """Fan-out hub: applies the optional RX hook then emits to all subscribers.

    All consumer slots (model ``on_messages`` / ``on_message``) are connected
    here during ``MainWindow`` initialisation.  The dispatcher itself has no
    state beyond the optional hook function.

    Signals:
        message_received: Emitted by :meth:`dispatch` with a single
            :class:`~cangui.can_message.CanMessage`.
        messages_received: Emitted by :meth:`dispatch_batch` with a
            ``list[CanMessage]`` batch.

    Args:
        parent: Optional Qt parent object.
    """

    message_received = Signal(CanMessage)
    messages_received = Signal(list)  # list[CanMessage]

    def __init__(self, parent=None):
        """Initialise the dispatcher with no hook and no connected consumers."""
        super().__init__(parent)
        self._rx_hook = None  # Callable[[int, bytes], bytes | None] | None

    def set_rx_hook(self, hook):
        """Install or clear the per-frame RX processing hook.

        The hook is called for every received frame before it is dispatched to
        consumers.  It can modify the payload or drop the frame entirely.

        Args:
            hook: ``Callable[[arb_id: int, data: bytes], bytes | None]`` —
                return modified ``bytes`` to forward the (possibly changed)
                frame, or ``None`` to drop it.  Pass ``None`` to clear any
                previously installed hook.
        """
        self._rx_hook = hook

    def dispatch(self, msg: CanMessage):
        """Dispatch a single frame through the hook and onto the single-frame signal.

        Called by :class:`~cangui.worker_trace_player.TracePlayer` for each
        replayed entry and by :meth:`~cangui.ui_main_window.MainWindow._load_trace`
        to feed the trace model during playback.

        Args:
            msg: The frame to dispatch.  The hook may mutate ``msg.data``
                in-place.

        Emits:
            message_received: If the frame passes the hook (or no hook is set).
        """
        if self._rx_hook is not None:
            result = self._rx_hook(msg.arbitration_id, msg.data)
            if result is None:
                return
            if result != msg.data:
                msg.data = result
        self.message_received.emit(msg)

    def dispatch_batch(self, messages: list):
        """Dispatch a batch of frames through the hook and onto the batch signal.

        Called by :class:`~cangui.worker_can_receiver.CanReceiver` via a Qt
        queued connection (delivery on the main thread).

        Args:
            messages: ``list[CanMessage]`` from the receiver.  If a hook is
                installed, frames may be dropped or modified.

        Emits:
            messages_received: With the (possibly filtered/modified) batch.
                The batch is never empty when emitted.
        """
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
