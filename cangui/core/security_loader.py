import importlib.util
from pathlib import Path
from typing import Callable


class SecurityLoader:
    """Loads an external Python file containing a seed-key algorithm.

    The file must define a function:
        def calculate_key(seed: bytes, security_level: int) -> bytes
    """

    def __init__(self):
        self._path: Path | None = None
        self._func: Callable[[bytes, int], bytes] | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_loaded(self) -> bool:
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

        self._path = path
        self._func = func

    def calculate_key(self, seed: bytes, security_level: int) -> bytes:
        """Compute the key from a seed using the loaded algorithm."""
        if self._func is None:
            raise RuntimeError("No security algorithm loaded")
        return self._func(seed, security_level)

    def unload(self):
        self._path = None
        self._func = None
