import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _config_path() -> Path:
    p = Path.home() / ".config" / "cangui"
    p.mkdir(parents=True, exist_ok=True)
    return p / "options.json"


@dataclass
class GeneralOptions:
    float_format: str = "f"
    decimal_places: int = 3
    timestamp_format: str = "relative"


@dataclass
class RxTxOptions:
    clear_on_reset: bool = True


@dataclass
class TracerOptions:
    buffer_size: int = 100000
    auto_scroll: bool = True
    trace_format: str = "trc"  # "trc" or "blf"


@dataclass
class PlotOptions:
    time_window: float = 10.0  # seconds
    max_display_points: int = 5000
    update_interval_ms: int = 50


@dataclass
class ConnectionDefaults:
    default_bitrate: int = 500000
    default_interface: str = "socketcan"


@dataclass
class AppOptions:
    general: GeneralOptions = field(default_factory=GeneralOptions)
    rx_tx: RxTxOptions = field(default_factory=RxTxOptions)
    tracer: TracerOptions = field(default_factory=TracerOptions)
    connection_defaults: ConnectionDefaults = field(default_factory=ConnectionDefaults)
    plot: PlotOptions = field(default_factory=PlotOptions)

    def save(self):
        with open(_config_path(), "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "AppOptions":
        path = _config_path()
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            return cls(
                general=GeneralOptions(**data.get("general", {})),
                rx_tx=RxTxOptions(**data.get("rx_tx", {})),
                tracer=TracerOptions(**data.get("tracer", {})),
                connection_defaults=ConnectionDefaults(**data.get("connection_defaults", {})),
                plot=PlotOptions(**data.get("plot", {})),
            )
        except Exception:
            return cls()
