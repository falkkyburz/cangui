"""Script API for use inside ``.script.py`` plugin files.

Exposes :func:`log` so that plugin scripts can send messages to the cangui
Log panel without importing any Qt classes.

Example usage in a plugin::

    from cangui.script_api import log

    def process_rx(arb_id: int, data: bytes) -> bytes | None:
        log(f"RX  0x{arb_id:03X}  {data.hex()}")
        return data
"""

_log_callback = None


def set_log_callback(callback) -> None:
    """Register the callable that routes log messages to the GUI Log panel.

    Called by :class:`~cangui.ui_main_window.MainWindow` at startup.

    Args:
        callback: A callable that accepts a single ``str`` argument and
            appends it to the Log panel.
    """
    global _log_callback
    _log_callback = callback


def log(message: str) -> None:
    """Send *message* to the cangui Log panel.

    If no callback has been registered (e.g. when running outside the GUI),
    the message is printed to stdout instead.

    Args:
        message: Text to display in the Log panel.
    """
    if _log_callback is not None:
        _log_callback(message)
    else:
        print(f"[script] {message}")
