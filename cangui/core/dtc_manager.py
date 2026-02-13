from dataclasses import dataclass


# DTC status bit masks (ISO 14229)
DTC_STATUS_TEST_FAILED = 0x01
DTC_STATUS_TEST_FAILED_THIS_CYCLE = 0x02
DTC_STATUS_PENDING = 0x04
DTC_STATUS_CONFIRMED = 0x08
DTC_STATUS_TEST_NOT_COMPLETED_SINCE_CLEAR = 0x10
DTC_STATUS_TEST_FAILED_SINCE_CLEAR = 0x20
DTC_STATUS_TEST_NOT_COMPLETED_THIS_CYCLE = 0x40
DTC_STATUS_WARNING_INDICATOR = 0x80


@dataclass
class Dtc:
    code: int  # 3-byte DTC code
    status: int  # Status byte

    @property
    def code_hex(self) -> str:
        return f"{self.code:06X}"

    @property
    def code_display(self) -> str:
        """Format as standard DTC string (e.g., P0123, C0456, B0789, U0ABC)."""
        first_byte = (self.code >> 16) & 0xFF
        prefix_bits = (first_byte >> 6) & 0x03
        prefix = ["P", "C", "B", "U"][prefix_bits]
        second_digit = (first_byte >> 4) & 0x03
        remaining = self.code & 0x0FFF
        third_digit = (first_byte) & 0x0F
        return f"{prefix}{second_digit}{third_digit:01X}{remaining:03X}"

    @property
    def is_active(self) -> bool:
        return bool(self.status & DTC_STATUS_TEST_FAILED)

    @property
    def is_confirmed(self) -> bool:
        return bool(self.status & DTC_STATUS_CONFIRMED)

    @property
    def is_pending(self) -> bool:
        return bool(self.status & DTC_STATUS_PENDING)

    @property
    def status_text(self) -> str:
        parts = []
        if self.status & DTC_STATUS_TEST_FAILED:
            parts.append("Active")
        if self.status & DTC_STATUS_CONFIRMED:
            parts.append("Confirmed")
        if self.status & DTC_STATUS_PENDING:
            parts.append("Pending")
        if self.status & DTC_STATUS_WARNING_INDICATOR:
            parts.append("Warning")
        if self.status & DTC_STATUS_TEST_FAILED_SINCE_CLEAR:
            parts.append("FailedSinceClear")
        return ", ".join(parts) if parts else "Inactive"

    @property
    def status_bits(self) -> str:
        return f"{self.status:08b}"


class DtcManager:
    """Parses DTC data from UDS ReadDTCInformation responses."""

    def parse_report_by_status_mask(self, data: bytes) -> list[Dtc]:
        """Parse response from sub-function 0x02 (reportDTCByStatusMask).

        Response format: [service_id, sub_function, status_availability_mask,
                          DTC_high, DTC_mid, DTC_low, status, ...]
        """
        dtcs = []
        if len(data) < 4:
            return dtcs
        # Skip service ID (0x59), sub-function, and availability mask
        payload = data[3:]
        # Each DTC record is 4 bytes: 3 bytes DTC code + 1 byte status
        while len(payload) >= 4:
            code = (payload[0] << 16) | (payload[1] << 8) | payload[2]
            status = payload[3]
            if code != 0:  # Skip all-zero DTCs
                dtcs.append(Dtc(code=code, status=status))
            payload = payload[4:]
        return dtcs

    def parse_raw(self, data: bytes) -> list[Dtc]:
        """Parse DTC records from raw 4-byte-per-DTC data (no header)."""
        dtcs = []
        while len(data) >= 4:
            code = (data[0] << 16) | (data[1] << 8) | data[2]
            status = data[3]
            if code != 0:
                dtcs.append(Dtc(code=code, status=status))
            data = data[4:]
        return dtcs
