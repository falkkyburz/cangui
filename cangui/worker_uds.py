from dataclasses import dataclass
from enum import Enum, auto
from queue import Queue, Empty

from PySide6.QtCore import QThread, Signal

from cangui.uds_client import UdsClient, UdsResponse


class UdsRequestType(Enum):
    CHANGE_SESSION = auto()
    ECU_RESET = auto()
    READ_DID = auto()
    WRITE_DID = auto()
    SECURITY_ACCESS = auto()
    TESTER_PRESENT = auto()
    RAW_REQUEST = auto()


@dataclass
class UdsRequest:
    request_type: UdsRequestType
    session: int = 0
    reset_type: int = 0x01
    did: int = 0
    data: bytes = b""
    security_level: int = 0x01
    seed_key_func: object = None  # callable(seed, level) -> key


class UdsWorker(QThread):
    """Executes UDS requests asynchronously on a background thread."""

    response_received = Signal(UdsResponse)
    error_occurred = Signal(str)

    def __init__(self, client: UdsClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._queue: Queue[UdsRequest] = Queue()
        self._running = False

    def execute(self, request: UdsRequest):
        """Queue a request and ensure the worker thread is running."""
        self._queue.put(request)
        if not self.isRunning():
            self._running = True
            self.start()

    def run(self):
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
        self._running = False
        self.wait(2000)
