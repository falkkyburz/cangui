"""UDS (Unified Diagnostic Services) façade for the cangui diagnostic window.

This module exposes a single ``UdsService`` QObject that the UI layer uses to
initiate all ISO 14229-1 diagnostic operations.  Under the hood it owns a
``UdsClient`` (which manages the ISO-TP transport layer) and a ``UdsWorker``
(a QThread that serialises requests and emits responses back to the GUI thread).

Typical lifecycle
-----------------
1. Instantiate ``UdsService`` (usually once, owned by the main window).
2. Call ``connect()`` when the user opens a CAN session.
3. Dispatch diagnostic requests via ``change_session()``, ``read_did()``, etc.
4. React to ``response_received`` / ``error_occurred`` signals in the UI.
5. Call ``disconnect()`` when the session ends or the bus is closed.
"""

import can
from PySide6.QtCore import QObject, Signal

from cangui.uds_client import UdsClient, UdsConfig, UdsResponse
from cangui.worker_uds import UdsWorker, UdsRequest, UdsRequestType


class UdsService(QObject):
    """Façade that bridges the diagnostic UI to the asynchronous UDS worker.

    All public methods simply build a ``UdsRequest`` dataclass and hand it to
    ``UdsWorker.execute()``.  The worker runs on a dedicated QThread, so the
    GUI thread is never blocked waiting for ISO-TP round-trips.  Responses and
    errors are delivered back to the GUI thread through queued Qt signal
    connections.

    Signals:
        response_received: Emitted by the underlying ``UdsWorker`` whenever a
            successful UDS response is received and forwarded here.
        error_occurred: Emitted with a human-readable error string when a
            request fails (negative response code, timeout, transport error, …).
        connection_changed: Emitted with ``True`` after a successful
            ``connect()`` call and with ``False`` after ``disconnect()``.
    """

    response_received = Signal(UdsResponse)
    """Forwarded from ``UdsWorker``; carries the decoded ``UdsResponse``."""

    error_occurred = Signal(str)
    """Forwarded from ``UdsWorker``; carries a human-readable error message."""

    connection_changed = Signal(bool)
    """Emitted with the new connected state whenever ``connect()`` or ``disconnect()`` is called."""

    def __init__(self, parent=None):
        """Initialise the service, creating the underlying client and worker.

        The ``UdsWorker`` thread is created here but not yet started; it starts
        lazily when the first request is executed.

        Args:
            parent: Optional Qt parent object that will own this service's
                lifetime.
        """
        super().__init__(parent)
        self._client = UdsClient()
        self._worker = UdsWorker(self._client, self)
        self._worker.response_received.connect(self.response_received)
        self._worker.error_occurred.connect(self.error_occurred)

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying ``UdsClient`` transport is currently open.

        Returns:
            ``True`` if the ISO-TP connection has been opened via ``connect()``
            and not yet closed by ``disconnect()``.
        """
        return self._client.is_open

    @property
    def config(self) -> UdsConfig:
        """Return the active UDS configuration (CAN IDs, timeouts, addressing mode).

        Returns:
            The ``UdsConfig`` currently held by the ``UdsClient``.  Changes
            applied to this object are reflected immediately in subsequent
            requests.
        """
        return self._client.config

    def connect(self, bus: can.BusABC, config: UdsConfig | None = None):
        """Open the UDS transport on *bus* and emit ``connection_changed(True)``.

        Args:
            bus: An already-opened ``python-can`` bus instance to use for
                ISO-TP communication.
            config: Optional ``UdsConfig`` overriding the client defaults (CAN
                request/response IDs, timeouts, functional addressing, etc.).
                When ``None`` the existing client configuration is kept.

        Emits:
            connection_changed: With value ``True`` after the transport is open.
        """
        self._client.open(bus, config)
        self.connection_changed.emit(True)

    def disconnect(self):
        """Stop the worker, close the transport, and emit ``connection_changed(False)``.

        Signals the ``UdsWorker`` to finish its current request (if any) and
        exit, then closes the underlying ISO-TP connection.  Safe to call even
        when not connected.

        Emits:
            connection_changed: With value ``False`` after the transport is closed.
        """
        self._worker.stop()
        self._client.close()
        self.connection_changed.emit(False)

    def change_session(self, session: int):
        """Request a UDS diagnostic session change (service 0x10).

        Args:
            session: Session type byte as defined by ISO 14229-1.  Common
                values are ``0x01`` (default session), ``0x02`` (programming
                session), and ``0x03`` (extended diagnostic session).

        Emits:
            response_received: On success, with the ECU's positive response.
            error_occurred: On NRC or timeout.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.CHANGE_SESSION, session=session
        ))

    def ecu_reset(self, reset_type: int = 0x01):
        """Send an ECU reset request (service 0x11).

        Args:
            reset_type: Reset sub-function byte.  ``0x01`` = hard reset,
                ``0x02`` = key off/on reset, ``0x03`` = soft reset.
                Defaults to ``0x01`` (hard reset).

        Emits:
            response_received: On success, with the ECU's positive response.
            error_occurred: On NRC or timeout.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.ECU_RESET, reset_type=reset_type
        ))

    def read_did(self, did: int):
        """Read a Data Identifier from the ECU (service 0x22 ReadDataByIdentifier).

        Args:
            did: 16-bit Data Identifier (DID) to read, e.g. ``0xF190`` for
                the VIN string.

        Emits:
            response_received: On success, carrying the raw DID payload in
                ``UdsResponse.data``.
            error_occurred: When the ECU returns a negative response code or
                the request times out.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.READ_DID, did=did
        ))

    def write_did(self, did: int, data: bytes):
        """Write a value to a Data Identifier on the ECU (service 0x2E WriteDataByIdentifier).

        Args:
            did: 16-bit Data Identifier (DID) to write.
            data: Raw byte payload to write.  The ECU will validate length and
                content; a mismatch may result in a negative response.

        Emits:
            response_received: On success, with the ECU's positive response.
            error_occurred: On NRC (e.g. ``0x22`` conditionsNotCorrect) or
                timeout.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.WRITE_DID, did=did, data=data
        ))

    def security_access(self, level: int, seed_key_func=None):
        """Unlock a security access level on the ECU (service 0x27 SecurityAccess).

        The two-step seed/key exchange is handled transparently by the worker:
        it first requests the seed (sub-function *level*), applies
        *seed_key_func* to compute the key, then sends the key (sub-function
        *level* + 1).

        Args:
            level: Security access level (odd sub-function byte for the seed
                request step), e.g. ``0x01`` for level 1.
            seed_key_func: Callable with signature ``(seed: bytes) -> bytes``
                that computes the key from the ECU-provided seed.  When
                ``None`` the worker's default algorithm is used (if any).

        Emits:
            response_received: On successful unlock.
            error_occurred: When the key is rejected (NRC ``0x35``
                invalidKey) or the request times out.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.SECURITY_ACCESS,
            security_level=level,
            seed_key_func=seed_key_func,
        ))

    def tester_present(self):
        """Send a TesterPresent keep-alive to prevent the ECU from timing out the session (service 0x3E).

        This is typically called on a periodic timer (e.g. every 2 s) while a
        non-default session is active.  The response is a single positive-response
        byte and is forwarded via ``response_received``.

        Emits:
            response_received: On success.
            error_occurred: On timeout or NRC.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.TESTER_PRESENT
        ))

    def raw_request(self, data: bytes):
        """Send a raw UDS payload without any service-level interpretation.

        Use this for services not directly supported by the higher-level
        methods, or for manual debugging sessions.

        Args:
            data: Complete UDS request payload starting with the service ID
                byte (e.g. ``bytes([0x19, 0x02, 0xFF])`` for
                ReadDTCInformation reportDTCByStatusMask, all DTCs).

        Emits:
            response_received: With the raw ECU response bytes.
            error_occurred: On NRC or timeout.
        """
        self._worker.execute(UdsRequest(
            request_type=UdsRequestType.RAW_REQUEST, data=data
        ))
