"""Seed-Key algorithm plugin example for cangui.

Naming convention: *.seedkey.py
Attach via Project Manager → "Attach Seed-Key Plugin".
The loaded algorithm is used automatically when performing
Security Access (0x27) from the Diagnostics window.

Interface
---------
    init(connections: list[dict]) -> None               [optional]
        Called when a CAN connection becomes active or is reset.
        ``connections`` holds the active bus configs (interface, channel,
        bitrate, fd, name, bus_number).  Use this to select the right
        algorithm variant for the connected ECU, if needed.

    calculate_key(seed: bytes, security_level: int) -> bytes  [required]
        Called with the seed received from the ECU.
        Return the computed key bytes to send back.

        seed           — raw seed bytes from the ECU's 0x27 seedResponse
        security_level — the requested access level (e.g. 0x01, 0x03, …)

This example
------------
A simple XOR-based placeholder.  Replace the body of calculate_key()
with the real ECU-specific formula supplied by the ECU manufacturer.

Algorithm: key[i] = (~seed[i] & 0xFF) XOR 0xA5 XOR (security_level & 0xFF)
"""

# ---------------------------------------------------------------------------
# Optional: init
# ---------------------------------------------------------------------------

def init(connections: list[dict]) -> None:
    """Called when a CAN connection becomes active or is reset.

    Use this to select the ECU variant, log the active bus, or
    pre-compute algorithm constants that depend on the channel config.
    """
    # Example: print active channels for debugging
    for conn in connections:
        print(f"[Seedkey] Active bus: {conn['interface']} {conn['channel']} "
              f"@ {conn['bitrate']} bit/s  (bus {conn['bus_number']})")


# ---------------------------------------------------------------------------
# Required: calculate_key
# ---------------------------------------------------------------------------

def calculate_key(seed: bytes, security_level: int) -> bytes:
    """Compute the security access key from the ECU seed.

    Replace this implementation with the real algorithm for your ECU.
    """
    offset = security_level & 0xFF
    return bytes(((~b & 0xFF) ^ 0xA5 ^ offset) for b in seed)
