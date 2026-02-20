"""Canonical CAN frame data structure used throughout cangui.

This module is intentionally free of Qt and python-can imports so it can be
used safely in any layer without introducing unwanted dependencies.
"""

from dataclasses import dataclass, field
import time


@dataclass
class CanMessage:
    """Immutable-by-convention representation of a single CAN frame.

    ``CanMessage`` is the application-wide currency for CAN data.  It is
    produced by :class:`~cangui.can_bus.CanBus` on receive, passed through
    :class:`~cangui.service_message_dispatcher.MessageDispatcher`, consumed
    by all models and services, and reconstructed when transmitting.

    The ``row`` field links TX frames back to their originating row in
    :class:`~cangui.model_tx_message.TxMessageModel` so the display can be
    updated after a successful send.

    Args:
        arbitration_id: 11-bit standard or 29-bit extended CAN ID.
        data: Frame payload bytes (0–8 bytes for Classical CAN, up to 64 for
            CAN FD).
        is_extended_id: ``True`` if the arbitration ID uses the 29-bit
            extended format.
        is_fd: ``True`` for CAN FD frames.
        is_remote_frame: ``True`` for RTR (remote transmission request) frames.
        is_error_frame: ``True`` if the hardware reported an error frame.
        is_rx: ``True`` for received frames; ``False`` for frames that were
            transmitted (only relevant for loopback/virtual buses).
        dlc: Data Length Code as reported by the hardware.  May differ from
            ``len(data)`` for CAN FD frames with padding.
        timestamp: Unix epoch timestamp of reception (seconds).  Defaults to
            ``time.time()`` at construction.
        bus: Logical bus number matching
            :attr:`~cangui.can_bus.BusConfig.bus_number`.
        channel: python-can channel string (e.g. ``"vcan0"``).
        row: TX model row index, or ``-1`` for frames not originating from the
            TX model.
    """

    arbitration_id: int
    data: bytes
    is_extended_id: bool = False
    is_fd: bool = False
    is_remote_frame: bool = False
    is_error_frame: bool = False
    is_rx: bool = True
    dlc: int = 0
    timestamp: float = field(default_factory=time.time)
    bus: int = 1
    channel: str = ""
    row: int = -1  # TX model row index (-1 = unknown/not a TX model frame)

    @property
    def id_hex(self) -> str:
        """CAN ID formatted as a zero-padded hex string.

        Returns:
            8-character hex string for extended IDs (29-bit); 3-character hex
            string for standard IDs (11-bit).
        """
        if self.is_extended_id:
            return f"{self.arbitration_id:08X}"
        return f"{self.arbitration_id:03X}"

    @property
    def frame_type(self) -> str:
        """Human-readable frame type label.

        Returns:
            One of ``"Error"``, ``"RTR"``, ``"FD"``, or ``"Data"``.
        """
        if self.is_error_frame:
            return "Error"
        if self.is_remote_frame:
            return "RTR"
        if self.is_fd:
            return "FD"
        return "Data"

    @property
    def data_hex(self) -> str:
        """Payload formatted as an uppercase hex string with byte separators.

        Returns:
            Space-separated two-digit hex bytes, e.g. ``"DE AD BE EF"``.
        """
        return " ".join(f"{b:02X}" for b in self.data)
