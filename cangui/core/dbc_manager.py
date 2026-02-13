from pathlib import Path

from cantools.database import Database, Message


class DbcManager:
    """Manages DBC/KCD database files using cantools."""

    def __init__(self):
        self._db = Database()
        self._files: list[Path] = []

    @property
    def files(self) -> list[Path]:
        return list(self._files)

    @property
    def database(self) -> Database:
        return self._db

    def load_file(self, path: str | Path) -> list[str]:
        """Load a DBC/KCD file. Returns list of message names added."""
        path = Path(path)
        if path in self._files:
            return []
        self._db.add_dbc_file(str(path))
        self._files.append(path)
        return [m.name for m in self._db.messages]

    def remove_file(self, path: str | Path):
        """Remove a loaded file by rebuilding the database without it."""
        path = Path(path)
        if path not in self._files:
            return
        self._files.remove(path)
        self._db = Database()
        for f in self._files:
            self._db.add_dbc_file(str(f))

    def get_message_by_id(self, arb_id: int) -> Message | None:
        try:
            return self._db.get_message_by_frame_id(arb_id)
        except KeyError:
            return None

    def get_message_by_name(self, name: str) -> Message | None:
        try:
            return self._db.get_message_by_name(name)
        except KeyError:
            return None

    def decode(self, arb_id: int, data: bytes) -> dict[str, object] | None:
        """Decode raw CAN data to signal name->value dict."""
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return None
        try:
            return msg_def.decode(data, decode_choices=True)
        except Exception:
            return None

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal values into raw CAN data."""
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return None
        try:
            return msg_def.encode(signal_data)
        except Exception:
            return None

    def get_signal_unit(self, arb_id: int, signal_name: str) -> str:
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return ""
        for sig in msg_def.signals:
            if sig.name == signal_name:
                return sig.unit or ""
        return ""

    @property
    def messages(self) -> list[Message]:
        return list(self._db.messages)

    def clear(self):
        self._db = Database()
        self._files.clear()
