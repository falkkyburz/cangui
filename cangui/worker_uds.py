"""QThread that executes UDS service requests without blocking the UI.

Requests are enqueued from the main thread via :meth:`UdsWorker.execute` and
processed sequentially by the worker using a stdlib ``Queue``.  The worker
exits automatically once the queue is drained rather than running a continuous
idle loop.
"""

from dataclasses import dataclass
from enum import Enum, auto
from queue import Queue, Empty

from PySide6.QtCore import QThread, Signal

from cangui.uds_client import UdsClient, UdsResponse


class UdsRequestType(Enum):
    """Enumeration of supported UDS service request types."""

    CHANGE_SESSION = auto()
    ECU_RESET = auto()
    READ_DID = auto()
    WRITE_DID = auto()
    SECURITY_ACCESS = auto()
    TESTER_PRESENT = auto()
    RAW_REQUEST = auto()


@dataclass
class UdsRequest:
    """Serialised UDS request passed through the worker queue.

    Fields are overloaded: only the fields relevant to
    :attr:`request_type` need to be populated.

    Attributes:
        request_type: Which UDS service to execute.
        session: Session ID for :attr:`CHANGE_SESSION` (e.g. ``0x02``
            for extended).
        reset_type: Reset sub-function for :attr:`ECU_RESET`
            (``0x01`` = hard reset).
        did: Data Identifier (16-bit) for :attr:`READ_DID` /
            :attr:`WRITE_DID`.
        data: Payload bytes for :attr:`WRITE_DID` or raw bytes for
            :attr:`RAW_REQUEST`.
        security_level: Access level (odd = seed request) for
            :attr:`SECURITY_ACCESS`.
        seed_key_func: Callable ``(seed: bytes, level: int) -> bytes``
            that computes the key from a seed, used for
            :attr:`SECURITY_ACCESS`.
    """

    request_type: UdsRequestType
    session: int = 0
    reset_type: int = 0x01
    did: int = 0
    data: bytes = b""
    security_level: int = 0x01
    seed_key_func: object = None  # callable(seed, level) -> key


class UdsWorker(QThread):
    """Executes UDS requests asynchronously on a background thread.

    The worker is started lazily: :meth:`execute` starts the thread on the
    first enqueued request and the thread exits once the queue is empty.
    Subsequent calls to :meth:`execute` restart the thread as needed.

    Signals:
        response_received: Emitted after each successful (or failed) UDS
            round-trip with the structured :class:`~cangui.uds_client.UdsResponse`.
        error_occurred: Emitted with an error message string if an unexpected
            exception occurs (e.g., transport layer failure).

    Args:
        client: Configured :class:`~cangui.uds_client.UdsClient` wrapping the
            ISO-TP stack.
        parent: Optional Qt parent object.
    """

    response_received = Signal(UdsResponse)
    error_occurred = Signal(str)

    def __init__(self, client: UdsClient, parent=None):
        """Bind the worker to *client*; the thread starts lazily on the first :meth:`execute` call."""
        super().__init__(parent)
        self._client = client
        self._queue: Queue[UdsRequest] = Queue()
        self._running = False

    def execute(self, request: UdsRequest):
        """Enqueue a UDS request and ensure the worker thread is running.

        Safe to call from the main thread.  If the worker thread has already
        exited (queue drained), it is restarted automatically.

        Args:
            request: The :class:`UdsRequest` to execute.
        """
        self._queue.put(request)
        if not self.isRunning():
            self._running = True
            self.start()

    def run(self):
        """Request processing loop — runs on the worker thread.

        Dequeues :class:`UdsRequest` objects and dispatches them to the
        appropriate :class:`~cangui.uds_client.UdsClient` method.  Exits
        when the queue has been idle for one full 100 ms timeout with no new
        items.

        Emits :attr:`response_received` on success or known failure, and
        :attr:`error_occurred` on unexpected exceptions.
        """
        self._running = True
        while self._running:
            try:
                req = self._queue.get(timeout=0.1)
            except Empty:
                if self._queue.empty():
                    break
                continue

            try:
                match req.request_type:
                    case UdsRequestType.CHANGE_SESSION:
                        resp = self._client.change_session(req.session)
                    case UdsRequestType.ECU_RESET:
                        resp = self._client.ecu_reset(req.reset_type)
                    case UdsRequestType.READ_DID:
                        resp = self._client.read_did(req.did)
                    case UdsRequestType.WRITE_DID:
                        resp = self._client.write_did(req.did, req.data)
                    case UdsRequestType.SECURITY_ACCESS:
                        resp = self._client.security_access(
                            req.security_level, req.seed_key_func
                        )
                    case UdsRequestType.TESTER_PRESENT:
                        resp = self._client.tester_present()
                    case UdsRequestType.RAW_REQUEST:
                        resp = self._client.raw_request(req.data)
                    case _:
                        resp = UdsResponse(
                            service_name="Unknown",
                            success=False,
                            error=f"Unknown request type: {req.request_type}",
                        )
                self.response_received.emit(resp)
            except Exception as e:
                self.error_occurred.emit(str(e))

        self._running = False

    def stop(self):
        """Signal the worker to stop and wait up to 2 seconds."""
        self._running = False
        self.wait(2000)
