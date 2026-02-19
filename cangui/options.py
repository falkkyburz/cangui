import json
import sys
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
    default_interface: str = "virtual" if sys.platform == "win32" else "socketcan"


@dataclass
class TabVisibilityOptions:
    receive_transmit: bool = True
    database: bool = True
    trace: bool = True
    plot: bool = True
    diagnostics: bool = False
    project_manager: bool = True
    watch: bool = True
    watch_did: bool = False
    dtc: bool = True
    rx_filter: bool = True
    plot_list: bool = True
    settings: bool = True
    help: bool = True
    log: bool = True


@dataclass
class AppOptions:
    general: GeneralOptions = field(default_factory=GeneralOptions)
    rx_tx: RxTxOptions = field(default_factory=RxTxOptions)
    tracer: TracerOptions = field(default_factory=TracerOptions)
    connection_defaults: ConnectionDefaults = field(default_factory=ConnectionDefaults)
    plot: PlotOptions = field(default_factory=PlotOptions)
    tabs: TabVisibilityOptions = field(default_factory=TabVisibilityOptions)

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
            # Filter to only known keys to avoid TypeError on old/extra fields
            def _filter(dc, d):
                return {k: v for k, v in d.items() if k in dc.__dataclass_fields__}
            return cls(
                general=GeneralOptions(**_filter(GeneralOptions, data.get("general", {}))),
                rx_tx=RxTxOptions(**_filter(RxTxOptions, data.get("rx_tx", {}))),
                tracer=TracerOptions(**_filter(TracerOptions, data.get("tracer", {}))),
                connection_defaults=ConnectionDefaults(
                    **_filter(ConnectionDefaults, data.get("connection_defaults", {}))),
                plot=PlotOptions(**_filter(PlotOptions, data.get("plot", {}))),
                tabs=TabVisibilityOptions(**_filter(TabVisibilityOptions, data.get("tabs", {}))),
            )
        except Exception:
            return cls()
