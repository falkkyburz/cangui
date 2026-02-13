from pathlib import Path

from cangui.core.dbc_manager import DbcManager
from cangui.core.odx_manager import OdxManager


class DatabaseManager:
    """Unified database manager for DBC/KCD and ODX files."""

    def __init__(self):
        self._dbc = DbcManager()
        self._odx = OdxManager()

    @property
    def dbc(self) -> DbcManager:
        return self._dbc

    @property
    def odx(self) -> OdxManager:
        return self._odx

    @property
    def files(self) -> list[Path]:
        return self._dbc.files + self._odx.files

    def load_file(self, path: str | Path) -> list[str]:
        """Load a database file (DBC/KCD/ODX/PDX). Returns list of names."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".dbc", ".kcd"):
            return self._dbc.load_file(path)
        if suffix in (".odx", ".pdx", ".odx-d"):
            return self._odx.load_file(path)
        raise ValueError(f"Unsupported database format: {suffix}")

    def remove_file(self, path: str | Path):
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".dbc", ".kcd"):
            self._dbc.remove_file(path)
        elif suffix in (".odx", ".pdx", ".odx-d"):
            self._odx.remove_file(path)

    def decode(self, arb_id: int, data: bytes) -> dict[str, object] | None:
        return self._dbc.decode(arb_id, data)

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal values into raw CAN data."""
        return self._dbc.encode(arb_id, signal_data)

    def get_symbol(self, arb_id: int) -> str:
        """Get message symbol name for an arbitration ID."""
        msg = self._dbc.get_message_by_id(arb_id)
        return msg.name if msg else ""

    def get_signal_unit(self, arb_id: int, signal_name: str) -> str:
        return self._dbc.get_signal_unit(arb_id, signal_name)

    def clear(self):
        self._dbc.clear()
        self._odx.clear()
