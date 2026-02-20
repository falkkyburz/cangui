"""Seed-key security algorithm loader for UDS SecurityAccess.

Plugins are plain Python files with a ``.seedkey.py`` extension that must
define ``calculate_key(seed: bytes, security_level: int) -> bytes``.
Loaded via :class:`SecurityLoader` and called by the UDS diagnostic window
during a SecurityAccess (0x27) exchange.
"""
import importlib.util
from pathlib import Path
from typing import Callable

EXTENSION = ".seedkey.py"


class SecurityLoader:
    """Loads an external Python file containing a seed-key algorithm.

    Convention: plugin files should use the extension ``.seedkey.py``
    (e.g. ``MyECU.seedkey.py``).

    The file must define:
        def calculate_key(seed: bytes, security_level: int) -> bytes

    Optionally:
        def init(connections: list[dict]) -> None
            Called when a CAN connection becomes active or is reset.
            ``connections`` is a list of dicts with keys:
              interface, channel, bitrate, fd, name, bus_number
    """

    def __init__(self):
        """Initialise the loader in the unloaded state."""
        self._path: Path | None = None
        self._func: Callable[[bytes, int], bytes] | None = None
        self._init_func: Callable | None = None

    @property
    def path(self) -> Path | None:
        """Absolute path of the loaded plugin file, or ``None`` when unloaded."""
        return self._path

    @property
    def is_loaded(self) -> bool:
        """``True`` when a ``calculate_key`` function has been loaded."""
        return self._func is not None

    def load(self, path: str | Path):
        """Load a Python file and extract the calculate_key function."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Security file not found: {path}")

        spec = importlib.util.spec_from_file_location("security_algo", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "calculate_key"):
            raise AttributeError(
                f"Security file must define 'calculate_key(seed, security_level)': {path}"
            )

        func = getattr(module, "calculate_key")
        if not callable(func):
            raise TypeError(f"'calculate_key' is not callable in: {path}")

        init_fn = getattr(module, "init", None)
        if init_fn is not None and not callable(init_fn):
            raise TypeError(f"'init' is not callable in: {path}")

        self._path = path
        self._func = func
        self._init_func = init_fn

    def call_init(self, connections: list[dict]):
        """Call the plugin's init() if defined."""
        if self._init_func is not None:
            try:
                self._init_func(connections)
            except Exception:
                pass

    def calculate_key(self, seed: bytes, security_level: int) -> bytes:
        """Compute the key from a seed using the loaded algorithm."""
        if self._func is None:
            raise RuntimeError("No security algorithm loaded")
        return self._func(seed, security_level)

    def unload(self):
        """Remove the loaded algorithm and mark the loader as unloaded."""
        self._path = None
        self._func = None
        self._init_func = None
