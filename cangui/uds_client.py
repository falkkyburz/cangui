"""UDS (ISO 14229) client built on top of udsoncan and python-can.

This module provides:

- :class:`UdsConfig` — transport configuration (TX/RX arbitration IDs,
  timeout).
- :class:`UdsResponse` — uniform result container returned by every service
  method.
- :class:`UdsClient` — high-level wrapper around :class:`udsoncan.client.Client`
  that exposes the most common UDS services and hides the ISO-TP / udsoncan
  wiring.

All service methods return a :class:`UdsResponse` rather than raising
exceptions, so callers can handle errors uniformly without try/except.
"""

from dataclasses import dataclass

import can
import isotp
import udsoncan
from udsoncan.client import Client
from udsoncan.connections import PythonIsoTpConnection


@dataclass
class UdsConfig:
    """Transport-layer configuration for a UDS session.

    Attributes:
        tx_id: 11-bit CAN arbitration ID used for outgoing UDS requests.
            Defaults to ``0x7E0`` (the standard tester physical address).
        rx_id: 11-bit CAN arbitration ID expected for ECU responses.
            Defaults to ``0x7E8``.
        timeout: Maximum time in seconds to wait for an ECU response.
            Applied to both the ``request_timeout`` and ``p2_timeout``
            udsoncan client configuration keys.  Defaults to ``2.0``.
    """

    tx_id: int = 0x7E0
    rx_id: int = 0x7E8
    timeout: float = 2.0


@dataclass
class UdsResponse:
    """Result container returned by every :class:`UdsClient` service method.

    Attributes:
        service_name: Human-readable name of the UDS service that produced
            this response (e.g. ``"ReadDID"``, ``"ECUReset"``).
        success: ``True`` if the ECU returned a positive response.
        data: Raw payload bytes from the positive response.  Empty for
            services that carry no payload (e.g. TesterPresent).
        did: Data Identifier involved in the request, or ``0`` for services
            that are not DID-based.
        nrc: Negative Response Code byte value (e.g. ``0x22`` for
            conditionsNotCorrect).  ``0`` on success.
        nrc_name: Human-readable name of the NRC as supplied by udsoncan.
            Empty string on success.
        error: Free-text error description for connectivity or parse errors.
            Empty string on success.
    """

    service_name: str
    success: bool
    data: bytes = b""
    did: int = 0
    nrc: int = 0
    nrc_name: str = ""
    error: str = ""

    @property
    def data_hex(self) -> str:
        """Response payload formatted as an upper-case hex string with spaces.

        Returns:
            A string such as ``"DE AD BE EF"``, or an empty string when
            :attr:`data` is empty.
        """
        return " ".join(f"{b:02X}" for b in self.data)


class UdsClient:
    """Wrapper around udsoncan providing a simplified UDS interface.

    Manages the full ISO-TP / udsoncan stack lifecycle:

    1. Accepts an existing :class:`can.BusABC` instance from the caller.
    2. Creates an :class:`isotp.CanStack` with the configured TX/RX IDs.
    3. Wraps the stack in a :class:`~udsoncan.connections.PythonIsoTpConnection`.
    4. Opens a :class:`~udsoncan.client.Client` with exception-raising disabled
       so that negative responses are returned as :class:`UdsResponse` objects
       rather than raised as exceptions.

    All service methods return :class:`UdsResponse` and never raise.

    Attributes:
        _bus: The underlying python-can bus, held only during an open session.
        _stack: ISO-TP CAN stack bridging raw CAN frames to UDS PDUs.
        _conn: udsoncan connection object wrapping ``_stack``.
        _client: udsoncan client used to send service requests.
        _config: Active :class:`UdsConfig` for the current or next session.
    """

    def __init__(self):
        """Initialise an idle UDS client with default configuration."""
        self._bus: can.BusABC | None = None
        self._stack: isotp.CanStack | None = None
        self._conn: PythonIsoTpConnection | None = None
        self._client: Client | None = None
        self._config = UdsConfig()

    @property
    def is_open(self) -> bool:
        """``True`` when a UDS session is active (client has been opened)."""
        return self._client is not None

    @property
    def config(self) -> UdsConfig:
        """The :class:`UdsConfig` used for the current or most recent session."""
        return self._config

    def open(self, bus: can.BusABC, config: UdsConfig | None = None):
        """Open a UDS session on the given python-can bus.

        Closes any existing session first, then builds the ISO-TP stack and
        udsoncan client using *config* (or the previously set configuration
        if *config* is ``None``).

        Args:
            bus: An open :class:`can.BusABC` instance to use for CAN
                communication.  The caller retains ownership; this client
                does not call :meth:`can.BusABC.shutdown` on it.
            config: Optional transport configuration.  When provided,
                replaces :attr:`config` for this and future sessions.

        Raises:
            isotp.can_layer.CanError: If the ISO-TP stack cannot be
                initialised on *bus*.
        """
        self.close()
        if config is not None:
            self._config = config
        self._bus = bus
        addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            txid=self._config.tx_id,
            rxid=self._config.rx_id,
        )
        self._stack = isotp.CanStack(bus=self._bus, address=addr)
        self._conn = PythonIsoTpConnection(self._stack)
        client_config = dict(udsoncan.configs.default_client_config)
        client_config["request_timeout"] = self._config.timeout
        client_config["p2_timeout"] = self._config.timeout
        client_config["exception_on_negative_response"] = False
        client_config["exception_on_invalid_response"] = False
        client_config["exception_on_unexpected_response"] = False
        self._client = Client(self._conn, config=client_config)
        self._client.open()

    def close(self):
        """Close the active UDS session and release all resources.

        Safe to call when no session is open.  Swallows any exception raised
        by udsoncan's :meth:`~udsoncan.client.Client.close` method.
        """
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._conn = None
        self._stack = None
        self._bus = None

    def change_session(self, session: int) -> UdsResponse:
        """Send a DiagnosticSessionControl request (service 0x10).

        Args:
            session: Target diagnostic session identifier (e.g. ``0x01``
                for defaultSession, ``0x02`` for programmingSession,
                ``0x03`` for extendedDiagnosticSession).

        Returns:
            A :class:`UdsResponse` with ``success=True`` on a positive
            response, or ``success=False`` with :attr:`UdsResponse.error`
            populated on failure.
        """
        if self._client is None:
            return UdsResponse(service_name="DiagnosticSessionControl",
                               success=False, error="Not connected")
        try:
            resp = self._client.change_session(session)
            return self._make_response("DiagnosticSessionControl", resp)
        except Exception as e:
            return UdsResponse(service_name="DiagnosticSessionControl",
                               success=False, error=str(e))

    def ecu_reset(self, reset_type: int = 0x01) -> UdsResponse:
        """Send an ECUReset request (service 0x11).

        Args:
            reset_type: Reset sub-type byte (e.g. ``0x01`` for hardReset,
                ``0x02`` for keyOffOnReset, ``0x03`` for softReset).
                Defaults to ``0x01`` (hardReset).

        Returns:
            A :class:`UdsResponse` indicating success or failure.
        """
        if self._client is None:
            return UdsResponse(service_name="ECUReset",
                               success=False, error="Not connected")
        try:
            resp = self._client.ecu_reset(reset_type)
            return self._make_response("ECUReset", resp)
        except Exception as e:
            return UdsResponse(service_name="ECUReset",
                               success=False, error=str(e))

    def read_did(self, did: int) -> UdsResponse:
        """Send a ReadDataByIdentifier request (service 0x22).

        Args:
            did: 16-bit Data Identifier to read (e.g. ``0xF190`` for
                Vehicle Identification Number).

        Returns:
            A :class:`UdsResponse` with :attr:`UdsResponse.data` containing
            the raw DID payload on success, or ``success=False`` on failure.
        """
        if self._client is None:
            return UdsResponse(service_name="ReadDID", success=False,
                               error="Not connected")
        try:
            resp = self._client.read_data_by_identifier(did)
            result = self._make_response("ReadDID", resp, did=did)
            if resp.valid and resp.positive:
                # Extract the DID value from the response
                raw = resp.service_data.values.get(did, b"")
                if isinstance(raw, bytes):
                    result.data = raw
                else:
                    result.data = bytes(str(raw), "utf-8")
            return result
        except Exception as e:
            return UdsResponse(service_name="ReadDID", success=False,
                               did=did, error=str(e))

    def write_did(self, did: int, value: bytes) -> UdsResponse:
        """Send a WriteDataByIdentifier request (service 0x2E).

        Args:
            did: 16-bit Data Identifier to write.
            value: Raw byte payload to write to the identifier.

        Returns:
            A :class:`UdsResponse` indicating success or failure.
        """
        if self._client is None:
            return UdsResponse(service_name="WriteDID", success=False,
                               error="Not connected")
        try:
            resp = self._client.write_data_by_identifier(did, value)
            return self._make_response("WriteDID", resp, did=did)
        except Exception as e:
            return UdsResponse(service_name="WriteDID", success=False,
                               did=did, error=str(e))

    def security_access(self, level: int, seed_key_func=None) -> UdsResponse:
        """Perform a SecurityAccess exchange (service 0x27).

        Executes the two-step seed/key handshake:

        1. Request seed (sub-function ``level``).
        2. Compute key by calling *seed_key_func(seed, level)*.
        3. Send key (sub-function ``level + 1``).

        If *seed_key_func* is ``None``, the method returns after step 1 with
        ``success=True`` and :attr:`UdsResponse.data` set to the raw seed
        bytes.  This allows the caller to inspect the seed before providing
        an algorithm.

        Args:
            level: Security access level byte for the seed request
                (e.g. ``0x01`` for level 1).  The key sub-function is
                derived automatically by the udsoncan library.
            seed_key_func: Optional callable with signature
                ``(seed: bytes, level: int) -> bytes`` that computes the
                security key from the received seed.  When ``None``, only
                the seed is retrieved.

        Returns:
            A :class:`UdsResponse`.  On full success (key accepted) the
            response has ``success=True``.  If no key function is supplied,
            ``success=True`` with :attr:`UdsResponse.data` equal to the seed
            and a non-empty :attr:`UdsResponse.error` explanation.
        """
        if self._client is None:
            return UdsResponse(service_name="SecurityAccess", success=False,
                               error="Not connected")
        try:
            # Request seed
            resp = self._client.request_seed(level)
            if not resp.valid or not resp.positive:
                return self._make_response("SecurityAccess(seed)", resp)
            seed = resp.service_data.seed

            if seed_key_func is None:
                return UdsResponse(
                    service_name="SecurityAccess",
                    success=True,
                    data=seed,
                    error="Seed received but no key algorithm provided",
                )

            # Compute key
            key = seed_key_func(seed, level)

            # Send key
            resp = self._client.send_key(level, key)
            return self._make_response("SecurityAccess", resp)
        except Exception as e:
            return UdsResponse(service_name="SecurityAccess", success=False,
                               error=str(e))

    def tester_present(self) -> UdsResponse:
        """Send a TesterPresent message (service 0x3E).

        Keeps the current diagnostic session alive by informing the ECU that
        the tester is still connected.

        Returns:
            A :class:`UdsResponse` indicating success or failure.
        """
        if self._client is None:
            return UdsResponse(service_name="TesterPresent",
                               success=False, error="Not connected")
        try:
            resp = self._client.tester_present()
            return self._make_response("TesterPresent", resp)
        except Exception as e:
            return UdsResponse(service_name="TesterPresent",
                               success=False, error=str(e))

    def raw_request(self, data: bytes) -> UdsResponse:
        """Send a raw UDS request payload and return the raw response.

        Bypasses the udsoncan service abstraction and writes *data* directly
        to the ISO-TP connection.  Useful for experimental or non-standard
        service requests.

        Args:
            data: Complete UDS request PDU (service ID byte followed by any
                sub-function or parameter bytes).

        Returns:
            A :class:`UdsResponse` with :attr:`UdsResponse.data` set to the
            raw response payload on success, or ``success=False`` on timeout
            or connection error.
        """
        if self._client is None:
            return UdsResponse(service_name="RawRequest",
                               success=False, error="Not connected")
        try:
            self._conn.send(data)
            payload = self._conn.wait_frame(self._config.timeout)
            if payload is None:
                return UdsResponse(service_name="RawRequest",
                                   success=False, error="Timeout")
            return UdsResponse(service_name="RawRequest",
                               success=True, data=bytes(payload))
        except Exception as e:
            return UdsResponse(service_name="RawRequest",
                               success=False, error=str(e))

    def _make_response(self, service_name: str, resp, did: int = 0) -> UdsResponse:
        """Convert a udsoncan response object into a :class:`UdsResponse`.

        Handles three cases:

        - Positive response: ``success=True`` with the raw payload.
        - Valid negative response: ``success=False`` with NRC details.
        - Invalid / absent response: ``success=False`` with a generic error.

        Args:
            service_name: Label to store in :attr:`UdsResponse.service_name`.
            resp: The raw udsoncan response object returned by a client
                service call.
            did: Optional DID to associate with the response.  Defaults to
                ``0`` for non-DID services.

        Returns:
            A fully populated :class:`UdsResponse`.
        """
        if resp.valid and resp.positive:
            data = b""
            if hasattr(resp, "original_payload"):
                data = bytes(resp.original_payload)
            return UdsResponse(service_name=service_name, success=True,
                               data=data, did=did)
        elif resp.valid and not resp.positive:
            nrc = resp.code if hasattr(resp, "code") else 0
            nrc_name = resp.code_name if hasattr(resp, "code_name") else ""
            return UdsResponse(service_name=service_name, success=False,
                               did=did, nrc=nrc, nrc_name=nrc_name,
                               error=f"NRC 0x{nrc:02X}: {nrc_name}")
        else:
            return UdsResponse(service_name=service_name, success=False,
                               did=did, error="Invalid response")
