"""Unified database manager for CAN and diagnostic description files.

This module provides :class:`DatabaseManager`, the single aggregation point
for all signal and diagnostic databases used by cangui.  It owns one
:class:`~cangui.dbc_manager.DbcManager` for CAN signal databases (DBC/KCD)
and one :class:`~cangui.odx_manager.OdxManager` for UDS diagnostic parameter
sets (ODX/PDX).  Callers never need to interact with the sub-managers
directly; all file I/O and decode/encode operations are routed through this
class.
"""

from pathlib import Path

from cangui.dbc_manager import DbcManager
from cangui.odx_manager import OdxManager


class DatabaseManager:
    """Unified database manager for DBC/KCD and ODX files.

    ``DatabaseManager`` acts as the central registry for all database files
    loaded during a session.  It dispatches file operations to the appropriate
    sub-manager based on the file extension and exposes a unified
    :meth:`decode` / :meth:`encode` API that higher-level components (such as
    :class:`~cangui.signal_decoder.SignalDecoder`) rely upon.

    A single instance is typically created at application start-up and shared
    across all windows and services via dependency injection.
    """

    def __init__(self):
        """Initialise the manager with empty DBC and ODX sub-managers."""
        self._dbc = DbcManager()
        self._odx = OdxManager()

    @property
    def dbc(self) -> DbcManager:
        """The underlying DBC/KCD sub-manager.

        Provides direct access to the cantools ``Database`` object and related
        helpers for callers that require signal-level introspection beyond
        what :class:`DatabaseManager` exposes.

        Returns:
            The :class:`~cangui.dbc_manager.DbcManager` instance owned by
            this manager.
        """
        return self._dbc

    @property
    def odx(self) -> OdxManager:
        """The underlying ODX/PDX sub-manager.

        Returns:
            The :class:`~cangui.odx_manager.OdxManager` instance owned by
            this manager.
        """
        return self._odx

    @property
    def files(self) -> list[Path]:
        """All currently loaded database file paths.

        Returns:
            A concatenated list of paths from the DBC sub-manager followed by
            paths from the ODX sub-manager.  The list is a snapshot; mutations
            do not affect the internal state.
        """
        return self._dbc.files + self._odx.files

    def load_file(self, path: str | Path) -> list[str]:
        """Load a database file and return the names of its top-level entries.

        Dispatches to :meth:`~cangui.dbc_manager.DbcManager.load_file` for
        ``.dbc`` and ``.kcd`` files, or to
        :meth:`~cangui.odx_manager.OdxManager.load_file` for ``.odx``,
        ``.pdx``, and ``.odx-d`` files.

        If the file is already loaded it is silently ignored and an empty list
        is returned.

        Args:
            path: Filesystem path to the database file.  Accepts both
                :class:`str` and :class:`~pathlib.Path`.  The file extension
                (case-insensitive) determines which sub-manager handles the
                load.

        Returns:
            A list of name strings extracted from the file:
            - DBC/KCD: message names added to the cantools database.
            - ODX/PDX: ECU variant short names found in the diagnostic layer
              hierarchy.
            Returns an empty list when the file was already loaded.

        Raises:
            ValueError: When the file extension is not recognised as a
                supported database format.
            Exception: Propagates any file I/O or parse errors raised by
                cantools or odxtools.
        """
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".dbc", ".kcd"):
            return self._dbc.load_file(path)
        if suffix in (".odx", ".pdx", ".odx-d"):
            return self._odx.load_file(path)
        raise ValueError(f"Unsupported database format: {suffix}")

    def remove_file(self, path: str | Path):
        """Unload a previously loaded database file.

        Dispatches to the appropriate sub-manager based on the file extension.
        If the file is not currently loaded the call is a no-op.

        For DBC/KCD files the entire cantools ``Database`` is rebuilt from the
        remaining files so that message definitions from the removed file are
        no longer available.

        For ODX/PDX files the variant list is rebuilt from the remaining
        databases.

        Args:
            path: Filesystem path of the file to remove.  Must match the path
                originally passed to :meth:`load_file` (compared as
                :class:`~pathlib.Path` objects).
        """
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".dbc", ".kcd"):
            self._dbc.remove_file(path)
        elif suffix in (".odx", ".pdx", ".odx-d"):
            self._odx.remove_file(path)

    def decode(self, arb_id: int, data: bytes) -> dict[str, object] | None:
        """Decode raw CAN payload bytes into a signal name-to-value mapping.

        Delegates to :meth:`~cangui.dbc_manager.DbcManager.decode`.  ODX
        databases do not participate in CAN signal decoding.

        Args:
            arb_id: CAN arbitration ID (11-bit standard or 29-bit extended) of
                the frame to decode.
            data: Raw payload bytes of the CAN frame.

        Returns:
            A dictionary mapping each signal name (``str``) to its decoded
            physical value (``float``, ``int``, or ``str`` for enumeration
            values).  Returns ``None`` when the arbitration ID is not found in
            any loaded DBC/KCD database or when decoding fails.
        """
        return self._dbc.decode(arb_id, data)

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal values into a raw CAN payload.

        Delegates to :meth:`~cangui.dbc_manager.DbcManager.encode`.

        Args:
            arb_id: CAN arbitration ID identifying the target message
                definition.
            signal_data: Mapping of signal name to physical value.  Values
                must be within the valid range defined for each signal in the
                database.

        Returns:
            A :class:`bytes` object containing the encoded CAN frame payload,
            or ``None`` when ``arb_id`` is unknown or encoding fails (e.g. a
            value is out of range).
        """
        return self._dbc.encode(arb_id, signal_data)

    def get_symbol(self, arb_id: int) -> str:
        """Return the message symbol name for a given arbitration ID.

        Args:
            arb_id: CAN arbitration ID to look up in the DBC/KCD databases.

        Returns:
            The message name string from the database (e.g. ``"EngineData"``),
            or an empty string when ``arb_id`` is not found.
        """
        msg = self._dbc.get_message_by_id(arb_id)
        return msg.name if msg else ""

    def get_signal_unit(self, arb_id: int, signal_name: str) -> str:
        """Return the engineering unit for a named signal within a message.

        Args:
            arb_id: CAN arbitration ID of the containing message.
            signal_name: Exact signal name as defined in the database.

        Returns:
            The unit string (e.g. ``"rpm"``, ``"km/h"``), or an empty string
            when the message or signal is not found, or when no unit is
            defined.
        """
        return self._dbc.get_signal_unit(arb_id, signal_name)

    def clear(self):
        """Unload all database files and reset both sub-managers to empty state.

        After this call, :attr:`files` returns an empty list and all decode/
        encode operations return ``None`` or empty results until new files are
        loaded.
        """
        self._dbc.clear()
        self._odx.clear()
