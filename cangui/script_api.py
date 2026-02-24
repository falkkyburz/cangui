"""Script API for use inside ``.script.py`` plugin files.

Exposes :func:`log` so that plugin scripts can send messages to the cangui
Log panel without importing any Qt classes. Logging is throttled by
default to avoid flooding the UI.

Example usage in a plugin::

    from cangui.script_api import log

    def process_rx(arb_id: int, data: bytes) -> bytes | None:
        log(f"RX  0x{arb_id:03X}  {data.hex()}")
        return data
"""

import time

_log_callback = None
_log_last: dict[str, float] = {}
_log_throttle_s = 1.0


def set_log_callback(callback) -> None:
    """Register the callable that routes log messages to the GUI Log panel.

    Called by :class:`~cangui.ui_main_window.MainWindow` at startup.

    Args:
        callback: A callable that accepts a single ``str`` argument and
            appends it to the Log panel.
    """
    global _log_callback
    _log_callback = callback


def set_log_throttle(throttle_s: float) -> None:
    """Set the default throttle window for :func:`log` in seconds."""
    global _log_throttle_s
    _log_throttle_s = max(0.0, float(throttle_s))


def log(message: str, *, key: str | None = None, throttle_s: float | None = None) -> None:
    """Send *message* to the cangui Log panel (throttled by default).

    If no callback has been registered (e.g. when running outside the GUI),
    the message is printed to stdout instead.

    Args:
        message: Text to display in the Log panel.
        key: Optional throttle key. Defaults to the message text.
        throttle_s: Optional throttle window in seconds. Use ``0`` or less to
            disable throttling for this call.
    """
    now = time.monotonic()
    throttle_window = _log_throttle_s if throttle_s is None else throttle_s
    if throttle_window > 0:
        throttle_key = message if key is None else key
        last = _log_last.get(throttle_key, 0.0)
        if (now - last) < throttle_window:
            return
        _log_last[throttle_key] = now

    if _log_callback is not None:
        _log_callback(message)
    else:
        print(f"[script] {message}")
