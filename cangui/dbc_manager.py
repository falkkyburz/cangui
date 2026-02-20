"""DBC and KCD CAN database manager using cantools.

This module provides :class:`DbcManager`, which wraps a single cantools
:class:`~cantools.database.Database` instance and manages the set of
DBC/KCD files that have been loaded into it.  It handles incremental file
loading and safe removal (by full database reconstruction), and exposes
decode/encode helpers used by the higher-level
:class:`~cangui.database_manager.DatabaseManager`.
"""

from pathlib import Path

from cantools.database import Database, Message


class DbcManager:
    """Manages DBC/KCD database files using cantools.

    Maintains a single cantools :class:`~cantools.database.Database` that
    accumulates message definitions from every loaded file.  File removal is
    implemented by discarding and rebuilding the database from the remaining
    files, which guarantees that orphaned definitions from the removed file
    do not persist.

    All public methods are **not** thread-safe; callers must ensure that file
    loading/removal operations are serialised (e.g. performed on the Qt main
    thread) while decode/encode may be called from worker threads provided no
    concurrent file operation is in progress.
    """

    def __init__(self):
        """Initialise the manager with an empty cantools database."""
        self._db = Database()
        self._files: list[Path] = []

    @property
    def files(self) -> list[Path]:
        """Snapshot list of all currently loaded database file paths.

        Returns:
            A new list containing the :class:`~pathlib.Path` of every file
            that has been successfully loaded.  Mutations to the returned list
            do not affect the internal state.
        """
        return list(self._files)

    @property
    def database(self) -> Database:
        """The underlying cantools ``Database`` instance.

        Provides direct access to the cantools API for callers that need
        functionality beyond what :class:`DbcManager` exposes directly.

        Returns:
            The live :class:`~cantools.database.Database` object containing all
            message definitions from the currently loaded files.
        """
        return self._db

    def load_file(self, path: str | Path) -> list[str]:
        """Load a DBC or KCD file and add its messages to the database.

        If the file is already loaded it is silently ignored and an empty
        list is returned.  After a successful load, all message definitions
        from the file are available for decode/encode operations.

        Args:
            path: Filesystem path to the ``.dbc`` or ``.kcd`` file to load.
                Accepts both :class:`str` and :class:`~pathlib.Path`.

        Returns:
            A list of all message names currently present in the combined
            database (not just the ones added by this file).  Returns an
            empty list when the file was already loaded.

        Raises:
            Exception: Propagates any parse error raised by cantools (e.g.
                :class:`cantools.database.errors.Error` for malformed files or
                :class:`OSError` for missing files).
        """
        path = Path(path)
        if path in self._files:
            return []
        self._db.add_dbc_file(str(path))
        self._files.append(path)
        return [m.name for m in self._db.messages]

    def remove_file(self, path: str | Path):
        """Remove a loaded DBC/KCD file and rebuild the database without it.

        Because cantools does not support incremental message removal, the
        entire internal database is discarded and reconstructed from all
        remaining files.  This operation is therefore O(n) in the number of
        loaded files.

        If ``path`` is not currently loaded the call is a no-op.

        Args:
            path: Filesystem path of the file to remove.  Must match the path
                originally passed to :meth:`load_file`.
        """
        path = Path(path)
        if path not in self._files:
            return
        self._files.remove(path)
        self._db = Database()
        for f in self._files:
            self._db.add_dbc_file(str(f))

    def get_message_by_id(self, arb_id: int) -> Message | None:
        """Look up a message definition by its CAN arbitration ID.

        Args:
            arb_id: 11-bit (standard frame) or 29-bit (extended frame) CAN
                arbitration ID to look up.

        Returns:
            The cantools :class:`~cantools.database.Message` object, or
            ``None`` when no message with the given ID is found in any loaded
            database.
        """
        try:
            return self._db.get_message_by_frame_id(arb_id)
        except KeyError:
            return None

    def get_message_by_name(self, name: str) -> Message | None:
        """Look up a message definition by its symbol name.

        Args:
            name: Message symbol name exactly as defined in the DBC/KCD file
                (case-sensitive).

        Returns:
            The cantools :class:`~cantools.database.Message` object, or
            ``None`` when no message with the given name is found.
        """
        try:
            return self._db.get_message_by_name(name)
        except KeyError:
            return None

    def decode(self, arb_id: int, data: bytes) -> dict[str, object] | None:
        """Decode raw CAN payload bytes to a signal name-to-value dictionary.

        Applies each signal's scale, offset, and optional enumeration mapping
        (``decode_choices=True``) so that the returned values are in physical
        engineering units.

        Args:
            arb_id: CAN arbitration ID of the frame to decode.
            data: Raw payload bytes.  The length should match the message
                definition; cantools may raise if it does not.

        Returns:
            A dictionary mapping signal name (``str``) to physical value
            (``float``, ``int``, or ``str`` for enumeration choices).  Returns
            ``None`` when ``arb_id`` is not found in any loaded database, or
            when cantools raises any exception during decoding (e.g. data
            length mismatch).
        """
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return None
        try:
            return msg_def.decode(data, decode_choices=True)
        except Exception:
            return None

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal physical values into a raw CAN payload.

        Args:
            arb_id: CAN arbitration ID identifying the target message
                definition.
            signal_data: Mapping of signal name to physical value.  Values
                should be within the valid range declared in the message
                definition; out-of-range values may cause cantools to raise.

        Returns:
            A :class:`bytes` object of the encoded CAN frame payload, or
            ``None`` when ``arb_id`` is not found in any loaded database, or
            when cantools raises any exception during encoding.
        """
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return None
        try:
            return msg_def.encode(signal_data)
        except Exception:
            return None

    def get_signal_unit(self, arb_id: int, signal_name: str) -> str:
        """Return the engineering unit string for a named signal.

        Args:
            arb_id: CAN arbitration ID of the message that contains the
                signal.
            signal_name: Exact signal name as defined in the database
                (case-sensitive).

        Returns:
            The unit string (e.g. ``"rpm"``, ``"km/h"``, ``"V"``), or an
            empty string when the message is not found, the signal is not
            found, or no unit is defined for the signal.
        """
        msg_def = self.get_message_by_id(arb_id)
        if msg_def is None:
            return ""
        for sig in msg_def.signals:
            if sig.name == signal_name:
                return sig.unit or ""
        return ""

    @property
    def messages(self) -> list[Message]:
        """Snapshot list of all message definitions across loaded files.

        Returns:
            A new list of cantools :class:`~cantools.database.Message` objects
            from all currently loaded DBC/KCD files.  Mutations to the
            returned list do not affect the internal database.
        """
        return list(self._db.messages)

    def clear(self):
        """Unload all files and reset the database to an empty state.

        Discards the current cantools ``Database`` instance and replaces it
        with a fresh empty one.  After this call, :attr:`files` returns an
        empty list and all decode/encode operations return ``None`` or empty
        results until new files are loaded.
        """
        self._db = Database()
        self._files.clear()
