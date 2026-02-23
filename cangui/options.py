"""Application-wide option dataclasses and persistence.

Options are stored in ``~/.config/cangui/options.json`` and survive project
switches.  Per-project overrides are stored in the project JSON under the
``settings`` key and are applied on top of the global options when a project
is opened.

The ``AppOptions`` class uses nested dataclasses so individual subsystems can
be passed only the slice they need (e.g., ``PlotWindow`` receives
``options.plot``).
"""

import sys
from dataclasses import dataclass, field


@dataclass
class GeneralOptions:
    """General display preferences.

    Attributes:
        float_format: Python format character for floating-point signal values.
            ``"f"`` for fixed-point, ``"e"`` for scientific notation.
        decimal_places: Number of decimal places shown for float signals.
        timestamp_format: ``"relative"`` (seconds since start) or
            ``"absolute"`` (Unix epoch).
    """

    float_format: str = "f"
    decimal_places: int = 3
    timestamp_format: str = "relative"


@dataclass
class RxTxOptions:
    """Options for the Receive/Transmit panel.

    Attributes:
        clear_on_reset: If ``True``, the RX message list is cleared when the
            connection is reset.
    """

    clear_on_reset: bool = True


@dataclass
class TracerOptions:
    """Options for the Trace recording subsystem.

    Attributes:
        buffer_size: Maximum number of entries retained in the display buffer
            (``deque`` maxlen in :class:`~cangui.model_trace.TraceModel`).
        auto_scroll: If ``True``, the trace view auto-scrolls to the latest
            entry while recording.
        trace_format: Output file format — ``"trc"`` (PEAK TRC v2.1) or
            ``"blf"`` (Vector BLF).
    """

    buffer_size: int = 100000
    auto_scroll: bool = True
    trace_format: str = "trc"  # "trc" or "blf"


@dataclass
class PlotOptions:
    """Options for the Plot subsystem.

    Attributes:
        time_window: Rolling time window in seconds.  Samples older than this
            are discarded from :class:`~cangui.service_plot_data.SignalBuffer`.
        max_display_points: Maximum number of points passed to pyqtgraph after
            LTTB downsampling.  Higher values are more accurate but slower.
        update_interval_ms: Interval (ms) between pyqtgraph ``setData()``
            calls in :class:`~cangui.ui_plot_window.PlotWindow`.
    """

    time_window: float = 10.0  # seconds
    max_display_points: int = 5000
    update_interval_ms: int = 50


@dataclass
class ConnectionDefaults:
    """Default values used when adding a new connection row.

    Attributes:
        default_bitrate: Pre-filled bitrate for new connections (bps).
        default_interface: Pre-filled interface name for new connections.
            Defaults to ``"virtual"`` on Windows and ``"socketcan"`` on Linux.
    """

    default_bitrate: int = 500000
    default_interface: str = "virtual" if sys.platform == "win32" else "socketcan"


@dataclass
class TabVisibilityOptions:
    """Per-tab visibility flags for the 3-pane layout.

    Each field corresponds to one dock panel.  Hidden tabs are removed from
    the ``QTabWidget`` via ``setTabVisible()`` but their widgets are not
    destroyed, so the underlying data (models, signals) is preserved.
    """

    receive_transmit: bool = True
    database: bool = True
    trace: bool = True
    plot: bool = True
    diagnostics: bool = False
    project_manager: bool = True
    watch: bool = True
    watch_did: bool = False
    dtc: bool = False
    rx_filter: bool = True
    plot_list: bool = True
    settings: bool = True
    help: bool = True
    log: bool = True


@dataclass
class AppOptions:
    """Root options object; owns all subsystem option groups.

    Persisted to ``~/.config/cangui/options.json`` as a flat JSON object with
    one key per subsystem.  Unknown or extra JSON keys are silently ignored so
    older option files can be loaded after a new version adds fields.

    Attributes:
        general: General display preferences.
        rx_tx: Receive/Transmit panel options.
        tracer: Trace recording options.
        connection_defaults: Defaults for new connection rows.
        plot: Plot subsystem options.
        tabs: Tab visibility flags.
    """

    general: GeneralOptions = field(default_factory=GeneralOptions)
    rx_tx: RxTxOptions = field(default_factory=RxTxOptions)
    tracer: TracerOptions = field(default_factory=TracerOptions)
    connection_defaults: ConnectionDefaults = field(default_factory=ConnectionDefaults)
    plot: PlotOptions = field(default_factory=PlotOptions)
    tabs: TabVisibilityOptions = field(default_factory=TabVisibilityOptions)

    def save(self):
        """No-op: settings are persisted in the project JSON, not a global file."""

    @classmethod
    def load(cls) -> "AppOptions":
        """Return a default :class:`AppOptions` instance.

        Settings are stored in the project JSON and applied on project open;
        no global config file is read.

        Returns:
            Default :class:`AppOptions` instance.
        """
        return cls()
