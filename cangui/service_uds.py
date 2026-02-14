import can
from PySide6.QtCore import QObject, Signal

from cangui.uds_client import UdsClient, UdsConfig, UdsResponse
from cangui.worker_uds import UdsWorker, UdsRequest, UdsRequestType


class UdsService(QObject):
    """Bridge between UI and UDS worker for asynchronous diagnostic requests."""

    response_received = Signal(UdsResponse)
    error_occurred = Signal(str)
    connection_changed = Signal(bool)  # connected state

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = UdsClient()
        self._worker = UdsWorker(self._client, self)
        self._worker.response_received.connect(self.response_received)
        self._worker.error_occurred.connect(self.error_occurred)

    @property
    def is_connected(self) -> bool:
        return self._client.is_open

    @property
    def config(self) -> UdsConfig:
        return self._client.config

    def connect(self, bus: can.BusABC, config: UdsConfig | None = None):
        self._client.open(bus, config)
        self.connection_changed.emit(True)

    def disconnect(self):
        self._worker.stop()
        self._client.close()
        self.connection_changed.emit(False)

    def change_session(self, session: int):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.CHANGE_SESSION, session=session
        ))

    def ecu_reset(self, reset_type: int = 0x01):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.ECU_RESET, reset_type=reset_type
        ))

    def read_did(self, did: int):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.READ_DID, did=did
        ))

    def write_did(self, did: int, data: bytes):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.WRITE_DID, did=did, data=data
        ))

    def security_access(self, level: int, seed_key_func=None):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.SECURITY_ACCESS,
            security_level=level,
            seed_key_func=seed_key_func,
        ))

    def tester_present(self):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.TESTER_PRESENT
        ))

    def raw_request(self, data: bytes):
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.RAW_REQUEST, data=data
        ))
