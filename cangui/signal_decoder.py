from dataclasses import dataclass

from cangui.database_manager import DatabaseManager


@dataclass
class DecodedSignal:
    name: str
    value: object  # float, int, or str (for enum choices)
    unit: str

    @property
    def display_value(self) -> str:
        if isinstance(self.value, float):
            return f"{self.value:.3f}"
        return str(self.value)


class SignalDecoder:
    """Decodes raw CAN message data into individual signals."""

    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager

    def decode(self, arb_id: int, data: bytes) -> list[DecodedSignal]:
        """Decode a CAN message into its constituent signals."""
        decoded = self._db.decode(arb_id, data)
        if decoded is None:
            return []
        result = []
        for name, value in decoded.items():
            unit = self._db.get_signal_unit(arb_id, name)
            result.append(DecodedSignal(name=name, value=value, unit=unit))
        return result

    def get_symbol(self, arb_id: int) -> str:
        return self._db.get_symbol(arb_id)

    def get_signals_for_id(self, arb_id: int) -> list[DecodedSignal]:
        """Return signal definitions for a given arbitration ID (with default values)."""
        msg = self._db.dbc.get_message_by_id(arb_id)
        if msg is None:
            return []
        return [
            DecodedSignal(
                name=sig.name,
                value=sig.initial if sig.initial is not None else 0,
                unit=sig.unit or "",
            )
            for sig in msg.signals
        ]

    def get_message_info(self, arb_id: int) -> tuple[int, int | None] | None:
        """Return (length, cycle_time_ms) for a message, or None if unknown."""
        msg = self._db.dbc.get_message_by_id(arb_id)
        if msg is None:
            return None
        return msg.length, msg.cycle_time

    def get_all_symbols(self) -> list[tuple[str, int]]:
        """Return sorted list of (symbol_name, frame_id) from all loaded DBCs."""
        result = []
        for msg in self._db.dbc.messages:
            result.append((msg.name, msg.frame_id))
        result.sort(key=lambda x: x[0])
        return result

    def get_id_by_symbol(self, name: str) -> int | None:
        """Return the frame ID for a symbol name, or None if not found."""
        msg = self._db.dbc.get_message_by_name(name)
        if msg is not None:
            return msg.frame_id
        return None

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal values into raw CAN data."""
        return self._db.encode(arb_id, signal_data)
