"""Signal decoding layer for CAN messages.

This module provides the ``SignalDecoder`` class and the ``DecodedSignal``
dataclass.  Together they form the presentation-facing API that converts raw
CAN byte payloads into named signal values expressed in physical engineering
units (e.g. km/h, °C, V).

The decoder delegates all database look-ups to a :class:`DatabaseManager`
instance so that it remains agnostic of the underlying file format (DBC, KCD,
or ODX).
"""

from dataclasses import dataclass

from cangui.database_manager import DatabaseManager


@dataclass
class DecodedSignal:
    """A single CAN signal that has been decoded from a raw message payload.

    Instances are produced by :meth:`SignalDecoder.decode` and
    :meth:`SignalDecoder.get_signals_for_id`.  The ``value`` field carries the
    *physical* value after applying the signal's scale/offset and any named
    enumeration mapping defined in the database.

    Attributes:
        name: Signal name exactly as it appears in the database (e.g.
            ``"EngineSpeed"``).
        value: Physical value after decoding.  The concrete type depends on the
            signal definition: ``float`` for numeric signals, ``int`` for
            unscaled integer signals, and ``str`` for enumeration/named-value
            signals (e.g. ``"RUNNING"``).
        unit: Engineering unit string taken from the database (e.g. ``"rpm"``,
            ``"km/h"``).  Empty string when no unit is defined.
        is_multiplexer: ``True`` when this signal acts as a multiplexer
            selector that controls which other signals are present in the same
            message.
        multiplexer_ids: List of multiplexer ID values for which this signal is
            active, or ``None`` when the signal is not multiplexed.
    """

    name: str
    value: object  # float, int, or str (for enum choices)
    unit: str
    is_multiplexer: bool = False
    multiplexer_ids: list[int] | None = None

    @property
    def display_value(self) -> str:
        """Return a human-readable string representation of the signal value.

        Floating-point values are formatted to three decimal places.  All other
        value types are converted via :func:`str`.

        Returns:
            A formatted string suitable for display in the UI, e.g.
            ``"123.456"`` for a float or ``"RUNNING"`` for an enum string.
        """
        if isinstance(self.value, float):
            return f"{self.value:.3f}"
        return str(self.value)


class SignalDecoder:
    """Decodes raw CAN message data into individual signals.

    ``SignalDecoder`` is the single entry-point used by the UI and service
    layers to translate raw CAN frames into structured :class:`DecodedSignal`
    objects.  It wraps a :class:`~cangui.database_manager.DatabaseManager` and
    adds multiplexer metadata enrichment on top of the plain decode result.

    This class does **not** own any database state; all file loading and
    unloading is managed through the injected ``DatabaseManager``.
    """

    def __init__(self, db_manager: DatabaseManager):
        """Initialise the decoder with a shared database manager.

        Args:
            db_manager: The :class:`~cangui.database_manager.DatabaseManager`
                instance that provides access to all loaded DBC/KCD/ODX
                databases.  The decoder holds a reference to this object; it
                does not take ownership.
        """
        self._db = db_manager

    def decode(self, arb_id: int, data: bytes) -> list[DecodedSignal]:
        """Decode a CAN message into its constituent signals.

        Looks up the message definition for ``arb_id`` in the loaded databases,
        applies scale/offset and enumeration mappings to each signal, and
        enriches the result with multiplexer metadata.

        Args:
            arb_id: 11-bit (standard) or 29-bit (extended) CAN arbitration ID
                of the message to decode.
            data: Raw payload bytes of the CAN frame.  The length must be
                compatible with the message definition; mismatched lengths may
                cause the underlying library to raise an exception, which is
                caught and results in an empty list being returned.

        Returns:
            A list of :class:`DecodedSignal` instances, one per signal defined
            in the message.  Returns an empty list when ``arb_id`` is not
            found in any loaded database or when decoding fails.
        """
        decoded = self._db.decode(arb_id, data)
        if decoded is None:
            return []
        # Build a lookup for mux metadata from the message definition
        msg = self._db.dbc.get_message_by_id(arb_id)
        sig_meta = {}
        if msg is not None:
            for sig in msg.signals:
                sig_meta[sig.name] = sig
        result = []
        for name, value in decoded.items():
            unit = self._db.get_signal_unit(arb_id, name)
            meta = sig_meta.get(name)
            result.append(DecodedSignal(
                name=name,
                value=value,
                unit=unit,
                is_multiplexer=meta.is_multiplexer if meta else False,
                multiplexer_ids=list(meta.multiplexer_ids) if meta and meta.multiplexer_ids is not None else None,
            ))
        return result

    def get_symbol(self, arb_id: int) -> str:
        """Return the message symbol name for a given arbitration ID.

        Args:
            arb_id: CAN arbitration ID to look up.

        Returns:
            The message name string from the database (e.g. ``"EngineData"``),
            or an empty string when ``arb_id`` is not found.
        """
        return self._db.get_symbol(arb_id)

    def get_signals_for_id(self, arb_id: int) -> list[DecodedSignal]:
        """Return signal definitions for a given arbitration ID with default values.

        This method is used to populate the TX editor with the signal list for
        a message before any real data has been received.  Each signal's value
        is set to the database-defined initial value, or ``0`` when no initial
        value is specified.

        Args:
            arb_id: CAN arbitration ID whose signal definitions should be
                returned.

        Returns:
            A list of :class:`DecodedSignal` instances with ``value`` set to
            the signal's initial value (or ``0``).  Returns an empty list when
            ``arb_id`` is not found in any loaded database.
        """
        msg = self._db.dbc.get_message_by_id(arb_id)
        if msg is None:
            return []
        return [
            DecodedSignal(
                name=sig.name,
                value=sig.initial if sig.initial is not None else 0,
                unit=sig.unit or "",
                is_multiplexer=sig.is_multiplexer,
                multiplexer_ids=list(sig.multiplexer_ids) if sig.multiplexer_ids is not None else None,
            )
            for sig in msg.signals
        ]

    def get_message_info(self, arb_id: int) -> tuple[int, int | None] | None:
        """Return the byte length and cycle time for a message definition.

        Args:
            arb_id: CAN arbitration ID of the message to query.

        Returns:
            A ``(length, cycle_time_ms)`` tuple where ``length`` is the
            expected payload size in bytes and ``cycle_time_ms`` is the
            nominal transmission period in milliseconds (``None`` when not
            specified in the database).  Returns ``None`` when ``arb_id`` is
            not found in any loaded database.
        """
        msg = self._db.dbc.get_message_by_id(arb_id)
        if msg is None:
            return None
        return msg.length, msg.cycle_time

    def get_all_symbols(self) -> list[tuple[str, int]]:
        """Return a sorted list of all message symbols from all loaded databases.

        Returns:
            A list of ``(symbol_name, frame_id)`` tuples sorted alphabetically
            by ``symbol_name``.  The list is empty when no databases are loaded.
        """
        result = []
        for msg in self._db.dbc.messages:
            result.append((msg.name, msg.frame_id))
        result.sort(key=lambda x: x[0])
        return result

    def get_id_by_symbol(self, name: str) -> int | None:
        """Return the frame ID for a message symbol name.

        Args:
            name: Message symbol name to look up (case-sensitive, as stored in
                the database).

        Returns:
            The integer frame ID (arbitration ID) of the matching message, or
            ``None`` when the name is not found in any loaded database.
        """
        msg = self._db.dbc.get_message_by_name(name)
        if msg is not None:
            return msg.frame_id
        return None

    def encode(self, arb_id: int, signal_data: dict[str, object]) -> bytes | None:
        """Encode signal values into a raw CAN payload.

        Delegates directly to :meth:`DatabaseManager.encode`.

        Args:
            arb_id: CAN arbitration ID identifying the target message
                definition.
            signal_data: Mapping of signal name to physical value.  All
                signals defined for the message should be present; missing
                signals may be padded with zeros by the underlying library.

        Returns:
            A :class:`bytes` object containing the encoded CAN payload, or
            ``None`` when ``arb_id`` is unknown or encoding fails (e.g. a
            value is out of range for its signal definition).
        """
        return self._db.encode(arb_id, signal_data)
