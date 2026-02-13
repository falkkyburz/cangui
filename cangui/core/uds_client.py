from dataclasses import dataclass

import can
import isotp
import udsoncan
from udsoncan.client import Client
from udsoncan.connections import PythonIsoTpConnection


@dataclass
class UdsConfig:
    tx_id: int = 0x7E0
    rx_id: int = 0x7E8
    timeout: float = 2.0


@dataclass
class UdsResponse:
    service_name: str
    success: bool
    data: bytes = b""
    did: int = 0
    nrc: int = 0
    nrc_name: str = ""
    error: str = ""

    @property
    def data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)


class UdsClient:
    """Wrapper around udsoncan providing a simplified UDS interface."""

    def __init__(self):
        self._bus: can.BusABC | None = None
        self._stack: isotp.CanStack | None = None
        self._conn: PythonIsoTpConnection | None = None
        self._client: Client | None = None
        self._config = UdsConfig()

    @property
    def is_open(self) -> bool:
        return self._client is not None

    @property
    def config(self) -> UdsConfig:
        return self._config

    def open(self, bus: can.BusABC, config: UdsConfig | None = None):
        """Open UDS connection on the given python-can bus."""
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
        """DiagnosticSessionControl (0x10)."""
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
        """ECUReset (0x11)."""
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
        """ReadDataByIdentifier (0x22)."""
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
        """WriteDataByIdentifier (0x2E)."""
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
        """SecurityAccess (0x27) — request seed then send key."""
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
        """TesterPresent (0x3E)."""
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
        """Send a raw UDS request and return the raw response."""
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
