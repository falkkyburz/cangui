"""AUTOSAR E2E Profile 2 plugin for cangui — applies only to frame 0x700.

Naming convention: *.script.py
Attach via Project Manager → "Attach Script Plugin".

All frames with an arb_id other than 0x700 are passed through unchanged.

Interface
---------
    init(connections)        — reset counters when bus comes up / is reset
    process_tx(arb_id, data) — fill in E2E counter and CRC in-place
    process_rx(arb_id, data) — validate E2E counter and CRC; drop on error
    log(message)             — write a message to the Log tab

DLC contract
------------
The plugin MUST return bytes of the same length as the input.
The frame layout for 0x700 (6 bytes, as defined in test.dbc):

  Byte 0  Temperature  (raw)
  Byte 1  Pressure     (raw)
  Bytes 2-3  Voltage   (raw)
  Byte 4  E2E_Counter in bits [7:4] (high nibble), bits [3:0] reserved = 0
  Byte 5  E2E_CRC  (CRC-8/AUTOSAR over DataID + payload + counter_byte)

AUTOSAR E2E Profile 2
---------------------
CRC-8/AUTOSAR: polynomial=0x2F  init=0xFF  refIn=False  refOut=False  xorOut=0xFF
               check value for "123456789" = 0xDF

DataIDList: 16 uint8 entries, indexed by the 4-bit counter (0–14).
Counter 0x0F is reserved ("no data available").
Counter wraps 0 → 14, skipping 15.
"""

# Script API import for Log tab output.
from cangui.script_api import log

# ---------------------------------------------------------------------------
# Configuration — replace with project-specific values
# ---------------------------------------------------------------------------

PROTECTED_ID = 0x700

DATA_ID_LIST: list[int] = [
    0x24, 0xA0, 0x30, 0x1C, 0x68, 0x57, 0x73, 0x85,
    0xF2, 0x4A, 0xBB, 0x93, 0x0E, 0x7D, 0xC9, 0x00,  # index 15 unused
]

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_tx_counters: dict[int, int] = {}
_rx_counters: dict[int, int] = {}
# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def init(connections: list[dict]) -> None:
    """Reset per-ID counters when the bus comes up or is reset."""
    _tx_counters.clear()
    _rx_counters.clear()
    log("E2E script initialized")

# ---------------------------------------------------------------------------
# CRC-8/AUTOSAR
# ---------------------------------------------------------------------------

def _crc8_autosar(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x2F) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ 0xFF

# ---------------------------------------------------------------------------
# TX hook
# ---------------------------------------------------------------------------

def process_tx(arb_id: int, data: bytes) -> bytes:
    """Fill counter (byte 4 high nibble) and CRC (byte 5) for frame 0x700.

    All other frames are returned unchanged.
    The returned bytes are always the same length as the input.
    """
    if arb_id != PROTECTED_ID or len(data) < 2:
        return data

    counter = _tx_counters.get(arb_id, 0)  # 0–14
    payload = data[:-2]                      # everything except last 2 bytes

    counter_byte = (counter << 4) & 0xFF    # counter in high nibble
    crc_input = bytes([DATA_ID_LIST[counter]]) + bytes(payload) + bytes([counter_byte, 0x00])
    crc = _crc8_autosar(crc_input)

    _tx_counters[arb_id] = (counter + 1) % 15  # 0→14, skip 15

    return bytes(payload) + bytes([counter_byte, crc])

# ---------------------------------------------------------------------------
# RX hook
# ---------------------------------------------------------------------------

def process_rx(arb_id: int, data: bytes) -> bytes | None:
    """Validate E2E header for frame 0x700; pass all other frames through.

    Returns the original (unmodified) frame on success so that all signals,
    including E2E_Counter and E2E_CRC, are visible in the RX / Watch views.
    Returns None to drop the frame on CRC mismatch, reserved counter, or
    duplicate counter.
    """
    if arb_id != PROTECTED_ID or len(data) < 2:
        return data

    payload = data[:-2]
    counter_byte = data[-2]
    received_crc = data[-1]

    counter = (counter_byte >> 4) & 0x0F
    if counter == 0x0F:
        log(
            f"E2E RX drop: reserved counter 0x0F on 0x{arb_id:03X}",
            key=f"{arb_id}:reserved_counter",
        )
        return None  # reserved "no data" value

    crc_input = bytes([DATA_ID_LIST[counter]]) + bytes(payload) + bytes([counter_byte, 0x00])
    if _crc8_autosar(crc_input) != received_crc:
        log(
            f"E2E RX drop: CRC mismatch on 0x{arb_id:03X}",
            key=f"{arb_id}:crc_mismatch",
        )
        return None  # CRC mismatch

    if _rx_counters.get(arb_id) == counter:
        log(
            f"E2E RX drop: duplicate counter {counter} on 0x{arb_id:03X}",
            key=f"{arb_id}:duplicate_counter",
        )
        return None  # duplicate counter

    _rx_counters[arb_id] = counter
    return data  # return full frame unchanged (same length)
