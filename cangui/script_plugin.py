import importlib.util
from pathlib import Path
from typing import Callable

EXTENSION = ".script.py"


class ScriptPlugin:
    """Loads an external Python file for per-frame RX/TX processing.

    Convention: plugin files should use the extension ``.script.py``
    (e.g. ``MyProject.script.py``).

    The file may define any combination of:

        def init(connections: list[dict]) -> None
            Called when a CAN connection becomes active or is reset.
            ``connections`` is a list of dicts with keys:
              interface, channel, bitrate, fd, name, bus_number

        def process_tx(arb_id: int, data: bytes) -> bytes
            Called before transmitting a CAN frame.
            Must return bytes of the same length as ``data``.
            Results with a different length are discarded.

        def process_rx(arb_id: int, data: bytes) -> bytes | None
            Called after receiving a CAN frame.
            Must return bytes of the same length as ``data``, or None to
            drop the frame entirely.
            Results with a different length are discarded (frame passes through).

    At least one of process_tx / process_rx must be defined.
    """

    def __init__(self):
        self._path: Path | None = None
        self._init_func: Callable | None = None
        self._process_tx: Callable | None = None
        self._process_rx: Callable | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_loaded(self) -> bool:
        return self._process_tx is not None or self._process_rx is not None

    def load(self, path: str | Path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Script plugin not found: {path}")

        spec = importlib.util.spec_from_file_location("script_plugin", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        tx = getattr(module, "process_tx", None)
        rx = getattr(module, "process_rx", None)
        init_fn = getattr(module, "init", None)

        if tx is None and rx is None:
            raise AttributeError(
                "Script plugin must define at least one of: process_tx, process_rx"
            )
        if tx is not None and not callable(tx):
            raise TypeError(f"'process_tx' is not callable in: {path}")
        if rx is not None and not callable(rx):
            raise TypeError(f"'process_rx' is not callable in: {path}")
        if init_fn is not None and not callable(init_fn):
            raise TypeError(f"'init' is not callable in: {path}")

        self._path = path
        self._init_func = init_fn
        self._process_tx = tx
        self._process_rx = rx

    def unload(self):
        self._path = None
        self._init_func = None
        self._process_tx = None
        self._process_rx = None

    def call_init(self, connections: list[dict]):
        """Call the plugin's init() if defined."""
        if self._init_func is not None:
            try:
                self._init_func(connections)
            except Exception:
                pass

    def apply_tx(self, arb_id: int, data: bytes) -> bytes:
        """Apply TX processing. Returns (possibly modified) same-length payload.

        If the plugin returns a result with a different length, the original
        data is used unchanged (DLC must not be modified).
        """
        if self._process_tx is None:
            return data
        try:
            result = self._process_tx(arb_id, data)
        except Exception:
            return data
        if result is None or len(result) != len(data):
            return data
        return result

    def apply_rx(self, arb_id: int, data: bytes) -> bytes | None:
        """Apply RX processing. Returns same-length payload, or None to drop.

        If the plugin returns a result with a different length, the original
        data is passed through (DLC must not be modified).
        """
        if self._process_rx is None:
            return data
        try:
            result = self._process_rx(arb_id, data)
        except Exception:
            return data
        if result is not None and len(result) != len(data):
            return data
        return result
