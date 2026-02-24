"""Main window module for cangui.

This module defines :class:`MainWindow`, the top-level QMainWindow that owns
every model, service, worker, and dock/tab window in the application.  It is
the single point where all subsystems are created, wired together, and torn
down.
"""

import sys
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QSplitter, QTabWidget, QApplication,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import Qt, QTimer, Signal

from cangui.can_bus import BusConfig
from cangui.can_message import CanMessage
from cangui.options import AppOptions
from cangui.project import Project
from cangui.database_manager import DatabaseManager
from cangui.signal_decoder import SignalDecoder
from cangui.trace_writer import TraceWriter, TraceFormat, create_trace_writer
from cangui.trace_reader import TraceReader, detect_trace_format
from cangui.service_message_dispatcher import MessageDispatcher
from cangui.service_can import CanService
from cangui.service_plot_data import PlotDataService
from cangui.service_plot_trace import PlotTraceService
from cangui.service_uds import UdsService
from cangui.uds_client import UdsConfig
from cangui.model_connection import ConnectionModel
from cangui.model_rx_message import RxMessageModel
from cangui.model_tx_message import TxMessageModel
from cangui.model_watch import WatchModel
from cangui.model_trace import TraceModel
from cangui.model_project import ProjectModel
from cangui.model_rx_filter import RxFilterModel
from cangui.ui_rx_tx_window import RxTxWindow
from cangui.ui_rx_filter_window import RxFilterWindow
from cangui.ui_watch_window import WatchWindow
from cangui.ui_project_window import ProjectWindow
from cangui.ui_trace_window import TraceWindow
from cangui.ui_replay_list_window import ReplayListWindow
from cangui.ui_plot_window import PlotWindow
from cangui.ui_diagnostic_window import DiagnosticWindow
from cangui.ui_watch_did_window import WatchDidWindow
from cangui.ui_dtc_window import DtcWindow
from cangui.ui_help_window import HelpWindow
from cangui.ui_settings_window import SettingsWindow
from cangui.ui_log_window import LogWindow
from cangui.ui_plot_list_window import PlotListWindow
from cangui.ui_database_window import DatabaseWindow
from cangui.script_plugin import ScriptPlugin
from cangui import script_api
from cangui.ui_focus_manager import FocusManager
from cangui.service_workspace import WorkspaceService
from cangui.worker_can_transmitter import CanTransmitter
from cangui.worker_ctb_player import CtbTracePlayer, ReplayMode
from cangui.trace_store import TraceStoreReader


@dataclass(frozen=True)
class _TxSendResult:
    """Post-send result payload for one TX attempt."""

    msg: CanMessage
    requested_data: bytes
    actual_data: bytes
    success: bool
    error: str
    connection_name: str
    endpoint: str


class MainWindow(QMainWindow):
    """Central application window that orchestrates all cangui subsystems.

    MainWindow is responsible for creating and owning every model, service,
    worker, and dock/tab window in the application, and for wiring them
    together via Qt signals and slots.

    Layout
    ------
    The central widget is a horizontal ``QSplitter`` with two panes:

    * **Main pane** (left) — a ``QTabWidget`` containing the
      Receive/Transmit, Database, Trace, Plot, and Diagnostics tabs.
    * **Right pane** — a vertical ``QSplitter`` with two further
      ``QTabWidget`` instances:

      * **Small pane** (top-right): Project Manager and Log tabs.
      * **List pane** (bottom-right): Watch, Watch DID, DTC, Rx Filter,
        Plot List, Settings, and Help tabs.

    Services
    --------
    * :class:`~cangui.service_can.CanService` — manages CAN bus connections
      and drives the :class:`~cangui.service_message_dispatcher.MessageDispatcher`.
    * :class:`~cangui.service_message_dispatcher.MessageDispatcher` — fans
      received messages out to models and services.
    * :class:`~cangui.service_plot_data.PlotDataService` — accumulates decoded
      signal samples for the live plot.
    * :class:`~cangui.service_plot_trace.PlotTraceService` — records plot
      traffic to trace files on disk.
    * :class:`~cangui.service_uds.UdsService` — UDS diagnostic client.
    * :class:`~cangui.service_workspace.WorkspaceService` — saves/restores
      splitter sizes and active tabs.

    Workers
    -------
    * :class:`~cangui.worker_can_transmitter.CanTransmitter` — periodic TX
      worker started on the first successful connection.
    * :class:`~cangui.worker_ctb_player.CtbTracePlayer` — replays a loaded
      CTB trace file with configurable speed, mode, and bus override.

    Signals
    -------
    _tx_display_update(int, bytes):
        Emitted from the TX worker thread to update a TX row's displayed data
        on the GUI thread (queued connection).
    """

    _tx_display_update = Signal(int, bytes)  # (row, actual_data) — cross-thread, queued
    _tx_send_result = Signal(object)  # _TxSendResult — cross-thread, queued

    def __init__(self, parent=None):
        """Initialise the main window and all application subsystems.

        Creates every model, service, worker, and child window in dependency
        order, wires their signals together, builds the 3-pane splitter
        layout, registers keyboard shortcuts and focus-manager entries, and
        populates the Help window with the shortcut reference table.

        Args:
            parent: Optional parent widget; normally ``None`` for a top-level
                window.
        """
        super().__init__(parent)
        self.setWindowTitle("cangui")
        screen = QApplication.primaryScreen().availableSize()
        self.resize(int(screen.width() * 0.8), int(screen.height() * 0.8))

        self._options = AppOptions.load()

        # Core
        self._project = Project()
        self._db_manager = DatabaseManager()
        self._decoder = SignalDecoder(self._db_manager)

        # Services
        self._dispatcher = MessageDispatcher(self)
        self._can_service = CanService(self._dispatcher, self)
        self._can_service.connection_status_changed.connect(self._on_connection_status)

        self._plot_service = PlotDataService(self._decoder, self)
        self._plot_service.time_window = self._options.plot.time_window
        self._plot_service.max_display_points = self._options.plot.max_display_points
        self._plot_trace_service = PlotTraceService(self)
        self._plot_trace_service.file_changed.connect(self._on_plot_trace_file_changed)
        self._plot_recording = False
        self._uds_service = UdsService(self)
        self._commit_plugin_tx_to_raw = False
        self._pending_tx_success: list[CanMessage] = []
        self._tx_success_timer = QTimer(self)
        self._tx_success_timer.setInterval(50)
        self._tx_success_timer.timeout.connect(self._flush_tx_success)
        self._tx_success_timer.start()

        # Models
        self._connection_model = ConnectionModel(self._can_service, self)
        self._rx_filter_model = RxFilterModel(self)
        self._rx_model = RxMessageModel(self._decoder, self._rx_filter_model, self)
        self._tx_model = TxMessageModel(self)
        self._tx_model.set_decoder(self._decoder)
        self._watch_model = WatchModel(self._decoder, self)
        self._trace_model = TraceModel(self)
        self._trace_model.set_trace_format(self._options.tracer.trace_format)
        self._project_model = ProjectModel(self._project, self)

        # Wire dispatcher — batch path (CAN receiver)
        self._dispatcher.messages_received.connect(self._rx_model.on_messages)
        self._dispatcher.messages_received.connect(self._watch_model.on_messages)
        self._dispatcher.messages_received.connect(self._trace_model.on_messages)
        self._dispatcher.messages_received.connect(self._plot_service.on_messages)
        self._dispatcher.messages_received.connect(self._plot_trace_service.on_messages)
        # Single-message path (trace player)
        self._dispatcher.message_received.connect(self._rx_model.on_message)
        self._dispatcher.message_received.connect(self._watch_model.on_message)
        self._dispatcher.message_received.connect(self._trace_model.on_message)
        self._dispatcher.message_received.connect(self._plot_service.on_message)
        self._dispatcher.message_received.connect(self._plot_trace_service.on_message)

        # TX transmitter (started when first connection is made)
        self._transmitter: CanTransmitter | None = None

        # CTB trace replay
        self._ctb_player: CtbTracePlayer | None = None

        # Script plugin
        self._script_plugin = ScriptPlugin()

        # Cross-thread TX result fan-in
        self._tx_send_result.connect(self._on_tx_send_result)

        # Create windows and layout
        self._create_layout()

        # Ensure trace folder is set even before any project is saved/loaded
        self._sync_trace_folder()

        # Keyboard shortcuts (no menu bar)
        self._create_shortcuts()

        # Focus manager
        self._focus = FocusManager(self)
        self._focus.register("X", self._rx_tx_win, self._main_tabs, "Receive/Transmit")
        self._focus.register("T", self._trace_win, self._main_tabs, "Trace")
        self._focus.register("P", self._plot_win, self._main_tabs, "Plot")
        self._focus.register("4", self._diag_win, self._main_tabs, "Diagnostics")
        self._focus.register("D", self._database_win, self._main_tabs, "Database")
        self._focus.register("M", self._project_win, self._small_tabs, "Project Manager")
        self._focus.register("L", self._log_win, self._small_tabs, "Log")
        self._focus.register("R", self._replay_list_win, self._list_tabs, "Replay")
        self._focus.register("W", self._watch_win, self._list_tabs, "Watch")
        self._focus.register("7", self._watch_did_win, self._list_tabs, "Watch DID")
        self._focus.register("8", self._dtc_win, self._list_tabs, "DTC")
        self._focus.register("F", self._rx_filter_win, self._list_tabs, "Rx Filter")
        self._focus.register("B", self._plot_list_win, self._list_tabs, "Plot List")
        self._focus.register("S", self._settings_win, self._list_tabs, "Settings")
        self._focus.register("H", self._help_win, self._list_tabs, "Help")
        self._focus.set_space_action(
            self._rx_tx_win._tx_view,
            self._rx_tx_win.send_space_pressed,
        )
        self._focus.install()

        # Wire script log callback to the Log panel
        script_api.set_log_callback(
            lambda msg: self._log_win.append("Script", msg)
        )
        script_api.set_log_throttle(self._options.script.log_throttle_s)

        # Populate help entries
        self._help_win.set_entries([
            ("X", "Receive/Transmit", "Window switch"),
            ("T", "Trace", "Window switch"),
            ("P", "Plot", "Window switch"),
            ("4", "Diagnostics", "Window switch"),
            ("D", "Database", "Window switch"),
            ("M", "Project Manager", "Window switch"),
            ("L", "Log", "Window switch"),
            ("W", "Watch", "Window switch"),
            ("7", "Watch DID", "Window switch"),
            ("8", "DTC", "Window switch"),
            ("F", "Rx Filter", "Window switch"),
            ("B", "Plot List", "Window switch"),
            ("S", "Settings", "Window switch"),
            ("H", "Help", "Window switch"),
            ("Space", "Send selected TX frame (Wait mode) / expand/collapse tree", "RX/TX / Tree views"),
            ("F9", "Start trace", "Trace"),
            ("F6", "Stop trace", "Trace"),
            ("Shift+F9", "Start all tracers", "Trace"),
            ("Shift+F6", "Stop all tracers", "Trace"),
            ("Ctrl+T", "Trace window", "Navigation"),
            ("Ctrl+R", "Receive/Transmit", "Navigation"),
            ("Ctrl+S", "Save project", "File"),
            ("Shift+Ctrl+S", "Save all", "File"),
            ("Alt+1..8", "Window switch (alternate)", "Navigation"),
            ("F11", "Full screen", "View"),
        ])


    def _create_layout(self):
        """Create all child windows and assemble the 3-pane splitter layout.

        Instantiates every dock/tab window, connects inter-window signals, and
        places them into three ``QTabWidget`` containers arranged inside a
        horizontal ``QSplitter`` (main pane on the left, a vertical
        ``QSplitter`` on the right holding the small pane above the list pane).

        Also creates the :class:`~cangui.service_workspace.WorkspaceService`
        which is responsible for persisting and restoring splitter proportions
        and active tab indices.  Default proportional ratios are set for the
        horizontal splitter (70 / 30), the vertical right splitter (30 / 70),
        and the internal RX/TX splitter (50 / 33 / 17).

        This method is called once during :meth:`__init__`.
        """
        # Create windows
        self._rx_tx_win = RxTxWindow(
            self._rx_model, self._tx_model, self._connection_model)
        self._rx_tx_win.add_tx_requested.connect(self._add_tx_frame)
        self._rx_tx_win.add_to_watch_requested.connect(self._add_signal_to_watch)
        self._rx_tx_win.add_to_plot_requested.connect(self._add_signal_to_plot)
        self._rx_tx_win.add_connection_requested.connect(self._add_connection)
        self._rx_tx_win.reset_connections_requested.connect(self._can_service.reset)
        self._rx_tx_win.reset_connections_requested.connect(self._rx_model.clear_errors)
        self._rx_tx_win.set_send_once_callback(self._send_message)

        self._diag_win = DiagnosticWindow(self._uds_service)
        self._diag_win.connect_requested.connect(self._uds_connect)
        self._diag_win.disconnect_requested.connect(self._uds_disconnect)

        self._plot_win = PlotWindow(self._plot_service)
        self._plot_win.record_toggled.connect(self._on_plot_record_toggled)
        self._plot_trace_service.file_changed.connect(self._plot_win.set_active_file)

        self._trace_win = TraceWindow(self._trace_model)
        self._trace_win.save_trace_requested.connect(self._save_trace)
        self._trace_win.start_recording_requested.connect(self._trace_start)
        self._trace_model.file_changed.connect(self._on_trace_file_changed)

        self._replay_list_win = ReplayListWindow()
        self._replay_list_win.load_trace_requested.connect(self._load_trace)
        self._replay_list_win.replay_requested.connect(self._on_replay_requested)
        self._replay_list_win.stop_requested.connect(self._on_trace_stop_requested)
        self._replay_list_win.pause_requested.connect(self._on_replay_pause_requested)
        self._replay_list_win.resume_requested.connect(self._on_replay_resume_requested)
        self._replay_list_win.reset_requested.connect(self._on_replay_reset_requested)

        self._log_win = LogWindow()
        self._log_win.message_appended.connect(
            lambda: self._small_tabs.setCurrentWidget(self._log_win))

        self._project_win = ProjectWindow(self._project_model)
        self._project_win.new_requested.connect(self._new_project)
        self._project_win.load_requested.connect(self._open_project)
        self._project_win.save_requested.connect(self._save_project)
        self._project_win.save_as_requested.connect(self._save_project_as)
        self._project_win.file_remove_requested.connect(self._remove_project_file)
        self._project_win.import_file_requested.connect(self._on_import_file)
        self._project_win.open_in_database_requested.connect(self._on_open_in_database)
        self._project_win.open_trace_requested.connect(self._on_open_trace_from_project)

        self._watch_win = WatchWindow(self._watch_model)
        self._watch_win.add_to_plot_requested.connect(self._add_signal_to_plot)

        self._watch_did_win = WatchDidWindow(self._uds_service)
        self._watch_did_win.add_to_plot_requested.connect(self._add_signal_to_plot)

        self._dtc_win = DtcWindow(self._uds_service)

        self._rx_filter_win = RxFilterWindow(self._rx_filter_model)

        self._settings_win = SettingsWindow(self._options)
        self._settings_win.setting_changed.connect(self._on_setting_changed)

        self._plot_list_win = PlotListWindow()
        self._plot_list_win.set_decoder(self._decoder)
        self._plot_list_win.signal_added.connect(self._plot_win.add_signal_curve)
        self._plot_list_win.signal_removed.connect(self._plot_win.remove_signal_curve)
        self._plot_list_win.signal_settings_changed.connect(self._plot_win.update_curve_style)
        self._plot_list_win.all_cleared.connect(self._plot_win.clear_all_curves)
        self._plot_list_win.signal_added.connect(
            lambda arb_id, *_: self._plot_trace_service.add_arb_id(arb_id))
        self._plot_list_win.signal_added.connect(lambda *_: self._auto_start_plot())
        self._plot_list_win.signal_removed.connect(
            lambda arb_id, _: self._plot_trace_service.remove_arb_id(arb_id))
        self._dispatcher.messages_received.connect(self._plot_list_win.on_messages)
        self._dispatcher.message_received.connect(self._plot_list_win.on_message)

        self._database_win = DatabaseWindow()
        self._database_win.dbc_imported.connect(self._on_dbc_imported)
        self._database_win.add_to_watch_requested.connect(self._add_signal_to_watch)
        self._database_win.add_to_plot_requested.connect(self._add_signal_to_plot)
        self._database_win.add_to_tx_requested.connect(self._add_db_message_to_tx)

        # 3-pane layout with QSplitter + QTabWidget
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Main pane (left) — tabs
        self._main_tabs = QTabWidget()
        self._main_tabs.setTabsClosable(False)
        self._main_tabs.setMovable(True)
        self._main_tabs.addTab(self._rx_tx_win, "Receive/Transmit [X]")
        self._main_tabs.addTab(self._database_win, "Database [D]")
        self._main_tabs.addTab(self._trace_win, "Trace [T]")
        self._main_tabs.addTab(self._plot_win, "Plot [P]")
        self._main_tabs.addTab(self._diag_win, "Diagnostics [4]")
        self._h_splitter.addWidget(self._main_tabs)

        # Right pane — vertical splitter
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Small pane (top-right)
        self._small_tabs = QTabWidget()
        self._small_tabs.setTabsClosable(False)
        self._small_tabs.setMovable(True)
        self._small_tabs.addTab(self._project_win, "Project Manager [M]")
        self._small_tabs.addTab(self._log_win, "Log [L]")
        self._v_splitter.addWidget(self._small_tabs)

        # List pane (bottom-right)
        self._list_tabs = QTabWidget()
        self._list_tabs.setTabsClosable(False)
        self._list_tabs.setMovable(True)
        self._list_tabs.setUsesScrollButtons(True)
        self._list_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._list_tabs.addTab(self._replay_list_win, "Replay [R]")
        self._list_tabs.addTab(self._watch_win, "Watch [W]")
        self._list_tabs.addTab(self._watch_did_win, "Watch DID [7]")
        self._list_tabs.addTab(self._dtc_win, "DTC [8]")
        self._list_tabs.addTab(self._rx_filter_win, "Rx Filter [F]")
        self._list_tabs.addTab(self._plot_list_win, "Plot List [B]")
        self._list_tabs.addTab(self._settings_win, "Settings [S]")

        self._help_win = HelpWindow()
        self._list_tabs.addTab(self._help_win, "Help [H]")

        self._v_splitter.addWidget(self._list_tabs)

        self._h_splitter.addWidget(self._v_splitter)

        self.setCentralWidget(self._h_splitter)

        # Default proportional ratios (updated when user drags a handle)
        self._h_ratios = [0.7, 0.3]
        self._v_ratios = [0.3, 0.7]
        self._rxtx_ratios = [0.5, 0.33, 0.17]

        # Track user-initiated splitter drags
        self._h_splitter.splitterMoved.connect(
            lambda: self._save_ratios(self._h_splitter, '_h_ratios'))
        self._v_splitter.splitterMoved.connect(
            lambda: self._save_ratios(self._v_splitter, '_v_ratios'))
        self._rx_tx_win.splitter.splitterMoved.connect(
            lambda: self._save_ratios(self._rx_tx_win.splitter, '_rxtx_ratios'))

        # Workspace service
        self._workspace_service = WorkspaceService(
            self._h_splitter,
            self._v_splitter,
            self._rx_tx_win.splitter,
            self._main_tabs,
            self._small_tabs,
            self._list_tabs,
        )

        self._apply_tab_visibility()

    def _create_shortcuts(self):
        """Register all application-wide keyboard shortcuts.

        Binds ``QShortcut`` instances to the main window for file operations
        (``Ctrl+S``), view navigation (``Alt+1``–``Alt+8``, ``Ctrl+R``,
        ``F11``), and trace control (``Ctrl+T``, ``F9``, ``F6``,
        ``Shift+F9``, ``Shift+F6``).

        This method is called once during :meth:`__init__`.
        """
        def _shortcut(key, slot):
            """Register a global keyboard shortcut binding *key* to *slot*.

            Args:
                key: Key sequence string (e.g. ``"Ctrl+S"``).
                slot: Callable to invoke when the shortcut is activated.

            Returns:
                The created :class:`QShortcut` instance.
            """
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(slot)
            return s

        # File
        _shortcut("Ctrl+S", self._save_project)
        _shortcut("Shift+Ctrl+S", self._save_project)

        # View — window switching
        _shortcut("Alt+1", lambda: self._focus.activate(5))   # Project Manager
        _shortcut("Alt+2", lambda: self._focus.activate(1))   # Trace
        _shortcut("Alt+3", lambda: self._focus.activate(2))   # Plot
        _shortcut("Alt+4", lambda: self._focus.activate(7))   # Watch
        _shortcut("Alt+5", lambda: self._focus.activate(4))   # Database
        _shortcut("Alt+6", lambda: self._focus.activate(3))   # Diagnostics
        _shortcut("Alt+7", lambda: self._focus.activate(9))   # DTC
        _shortcut("Alt+8", lambda: self._focus.activate(8))   # Watch DID
        _shortcut("Ctrl+R", lambda: self._focus.activate(0))  # Receive/Transmit
        _shortcut("F11", self._toggle_fullscreen)

        # Trace
        _shortcut("Ctrl+T", lambda: self._focus.activate(1))
        _shortcut("F9", self._trace_start)
        _shortcut("F6", self._trace_stop)
        _shortcut("Shift+F9", self._trace_start)
        _shortcut("Shift+F6", self._trace_stop)

    # -- Connection management --

    def _add_connection(self):
        """Add a new empty CAN connection row and switch to the RX/TX tab."""
        self._connection_model.add_empty_row()
        self._main_tabs.setCurrentWidget(self._rx_tx_win)

    def _on_connection_status(self, index: int, status: str):
        """React to a CAN connection status change.

        Starts the TX transmitter worker and invokes plugin ``init()``
        callbacks when a connection transitions to the ``"OK"`` state.

        Args:
            index: Index of the connection whose status changed.
            status: New status string; ``"OK"`` indicates a live connection.
        """
        if status == "OK":
            self._ensure_transmitter()
            self._call_plugin_inits()
            return

        if not (0 <= index < len(self._can_service.connections)):
            return

        conn = self._can_service.connections[index]
        conn_name = conn.name
        endpoint = f"{conn.config.interface}:{conn.config.channel}"
        if status == "Bus Heavy":
            self._log_win.append(
                "WARNING",
                f"Connection '{conn_name}' is Bus Heavy ({endpoint}).",
            )
        elif status == "Bus Off" or status.startswith("Error"):
            self._log_win.append(
                "ERROR",
                f"Connection '{conn_name}' status changed to {status} ({endpoint}).",
            )

    def _call_plugin_inits(self):
        """Pass current active connections to all loaded plugins' ``init()``.

        Builds a list of connection-descriptor dicts from every currently
        connected bus and forwards it to the script plugin and the
        diagnostic security (seed-key) plugin if either is loaded.
        """
        connections = [
            {
                "interface": conn.config.interface,
                "channel": conn.config.channel,
                "bitrate": conn.config.bitrate,
                "fd": conn.config.fd,
                "name": conn.config.name,
                "bus_number": conn.config.bus_number,
            }
            for conn in self._can_service.connections
            if conn.bus.is_connected
        ]
        if self._script_plugin.is_loaded:
            self._script_plugin.call_init(connections)
        if self._diag_win._security_loader.is_loaded:
            self._diag_win._security_loader.call_init(connections)

    def _ensure_transmitter(self):
        """Start the TX transmitter worker if not already running.

        Creates a :class:`~cangui.worker_can_transmitter.CanTransmitter`,
        wires its ``counts_updated`` signal to the TX model, connects the
        ``_tx_display_update`` cross-thread signal, and starts the worker
        thread.  Subsequent calls are no-ops.
        """
        if self._transmitter is not None:
            return
        self._transmitter = CanTransmitter(self._tx_model, self._send_message, self)
        self._transmitter.counts_updated.connect(self._tx_model.increment_counts)
        self._tx_display_update.connect(self._tx_model.update_last_sent)
        self._transmitter.start()

    def _send_message(self, msg):
        """Send a CAN message through the appropriate connected bus.

        If a script plugin is loaded, its ``apply_tx`` hook may modify the
        outgoing payload. Exactly one ``_tx_send_result`` event is emitted per
        call; all UI/model updates happen in ``_on_tx_send_result``.

        Args:
            msg: A :class:`~cangui.can_message.CanMessage` instance to
                transmit.

        Returns:
            ``True`` when sent successfully, ``False`` on failure.
        """
        # Resolve the target bus first so the script hook is never called
        # when no connection is active (e.g. during cyclic transmission while
        # the bus is disconnected).
        requested_data = bytes(msg.data)
        target = None
        for conn in self._can_service.connections:
            if conn.bus.is_connected and conn.config.bus_number == msg.bus:
                target = conn
                break
        if target is None:
            for conn in self._can_service.connections:
                if conn.bus.is_connected:
                    target = conn
                    break
        if target is None:
            self._tx_send_result.emit(_TxSendResult(
                msg=CanMessage(
                    arbitration_id=msg.arbitration_id,
                    data=bytes(msg.data),
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                    is_remote_frame=msg.is_remote_frame,
                    is_error_frame=msg.is_error_frame,
                    is_rx=False,
                    dlc=msg.dlc,
                    timestamp=msg.timestamp,
                    bus=msg.bus,
                    channel=msg.channel,
                    row=msg.row,
                ),
                requested_data=requested_data,
                actual_data=bytes(msg.data),
                success=False,
                error="No connected bus",
                connection_name=f"Bus {msg.bus}",
                endpoint="unbound",
            ))
            return False

        # Connected transport exists, but not in a sendable state.
        # Treat this as a skipped TX attempt, not a transmission failure.
        if target.status != "OK":
            self._tx_send_result.emit(_TxSendResult(
                msg=CanMessage(
                    arbitration_id=msg.arbitration_id,
                    data=bytes(msg.data),
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                    is_remote_frame=msg.is_remote_frame,
                    is_error_frame=msg.is_error_frame,
                    is_rx=False,
                    dlc=msg.dlc,
                    timestamp=msg.timestamp,
                    bus=msg.bus,
                    channel=target.config.channel,
                    row=msg.row,
                ),
                requested_data=requested_data,
                actual_data=bytes(msg.data),
                success=False,
                error=f"Bus disabled ({target.status})",
                connection_name=target.name,
                endpoint=f"{target.config.interface}:{target.config.channel}",
            ))
            return False

        needs_fd = msg.is_fd or msg.dlc > 8 or len(msg.data) > 8
        if needs_fd and not target.config.fd:
            self._tx_send_result.emit(_TxSendResult(
                msg=CanMessage(
                    arbitration_id=msg.arbitration_id,
                    data=bytes(msg.data),
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                    is_remote_frame=msg.is_remote_frame,
                    is_error_frame=msg.is_error_frame,
                    is_rx=False,
                    dlc=msg.dlc,
                    timestamp=msg.timestamp,
                    bus=msg.bus,
                    channel=target.config.channel,
                    row=msg.row,
                ),
                requested_data=requested_data,
                actual_data=bytes(msg.data),
                success=False,
                error="CAN FD disabled on connection",
                connection_name=target.name,
                endpoint=f"{target.config.interface}:{target.config.channel}",
            ))
            return False

        if self._script_plugin.is_loaded:
            data = self._script_plugin.apply_tx(msg.arbitration_id, msg.data)
            if data != msg.data:
                msg.data = data
        actual_data = bytes(msg.data)
        endpoint = f"{target.config.interface}:{target.config.channel}"
        try:
            target.bus.send(msg)
        except Exception as exc:
            self._tx_send_result.emit(_TxSendResult(
                msg=CanMessage(
                    arbitration_id=msg.arbitration_id,
                    data=actual_data,
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                    is_remote_frame=msg.is_remote_frame,
                    is_error_frame=msg.is_error_frame,
                    is_rx=False,
                    dlc=msg.dlc,
                    timestamp=msg.timestamp,
                    bus=msg.bus,
                    channel=target.config.channel,
                    row=msg.row,
                ),
                requested_data=requested_data,
                actual_data=actual_data,
                success=False,
                error=str(exc),
                connection_name=target.name,
                endpoint=endpoint,
            ))
            return False

        self._tx_send_result.emit(_TxSendResult(
            msg=CanMessage(
                arbitration_id=msg.arbitration_id,
                data=actual_data,
                is_extended_id=msg.is_extended_id,
                is_fd=msg.is_fd,
                is_remote_frame=msg.is_remote_frame,
                is_error_frame=msg.is_error_frame,
                is_rx=False,
                dlc=msg.dlc,
                timestamp=msg.timestamp,
                bus=msg.bus,
                channel=target.config.channel,
                row=msg.row,
            ),
            requested_data=requested_data,
            actual_data=actual_data,
            success=True,
            error="",
            connection_name=target.name,
            endpoint=endpoint,
        ))
        return True

    def _on_tx_send_result(self, result: _TxSendResult):
        """Handle one TX send result on the GUI thread."""
        if not result.success:
            if result.error == "No connected bus" or result.error.startswith("Bus disabled ("):
                return
            self._log_win.append(
                "ERROR",
                f"TX send failed on '{result.connection_name}' ({result.endpoint}): {result.error}",
            )
            return

        row = result.msg.row
        if row >= 0:
            if self._commit_plugin_tx_to_raw and result.actual_data != result.requested_data:
                idx = self._tx_model.index(row, 7)
                self._tx_model.setData(
                    idx,
                    " ".join(f"{b:02X}" for b in result.actual_data),
                    Qt.ItemDataRole.EditRole,
                )
            else:
                self._tx_model.update_last_sent(row, result.actual_data)

        # Batch-success path: keep per-send handler lightweight.
        self._pending_tx_success.append(result.msg)

    def _flush_tx_success(self):
        """Fan out successful TX frames in batches to reduce UI-thread churn."""
        if not self._pending_tx_success:
            return
        batch = self._pending_tx_success
        self._pending_tx_success = []

        # Trace model needs explicit Tx direction per frame.
        for msg in batch:
            self._trace_model.on_message(msg, "Tx")
        self._watch_model.on_messages(batch)
        self._plot_list_win.on_messages(batch)
        self._plot_service.on_messages(batch)

    def set_commit_plugin_tx_to_raw(self, enabled: bool):
        """Set whether successful plugin TX output should overwrite row raw_data."""
        self._commit_plugin_tx_to_raw = bool(enabled)

    # -- TX management --

    def _add_tx_frame(self):
        """Add a new empty TX message row and focus its CAN-ID cell for editing."""
        buses = [c.config.bus_number for c in self._can_service.connections] or [1]
        self._tx_model.add_empty_message(bus=buses[0])
        self._rx_tx_win.edit_last_tx_can_id()

    def _add_db_message_to_tx(self, can_id: int, dlc: int, is_extended: bool,
                              symbol: str, cycle_ms: int, bus: int):
        """Add a database-defined message to the TX list and switch to the RX/TX tab.

        If the requested bus number is not among the active connections the
        first available bus is used instead.  The default cycle time falls
        back to 100 ms when ``cycle_ms`` is 0.

        Args:
            can_id: CAN arbitration ID.
            dlc: Data length code (number of bytes).
            is_extended: ``True`` for a 29-bit extended CAN ID.
            symbol: Human-readable message name from the database.
            cycle_ms: Desired cyclic transmit interval in milliseconds.
            bus: Target bus number as configured in the connection settings.
        """
        from cangui.model_tx_message import TxMessageItem
        buses = [c.config.bus_number for c in self._can_service.connections] or [1]
        effective_bus = bus if bus in buses else buses[0]
        self._tx_model.add_message(TxMessageItem(
            bus=effective_bus,
            can_id=can_id,
            is_extended_id=is_extended,
            dlc=dlc,
            length=dlc,
            symbol=symbol,
            raw_data=bytearray(dlc),
            cycle_time_ms=cycle_ms if cycle_ms > 0 else 100,
        ))

    # -- DBC / Database management --

    def _on_dbc_imported(self, path: str):
        """Handle a successful DBC import from the Database view.

        Loads the file into the :class:`~cangui.database_manager.DatabaseManager`,
        registers the path in the project, and refreshes the RX symbol and TX
        signal decorations.  Displays a warning dialog if loading fails.

        Args:
            path: Absolute filesystem path to the imported ``.dbc`` file.
        """
        try:
            self._db_manager.load_file(path)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Import Error", f"Failed to load database:\n{e}")
            return
        self._project.add_database_file(path)
        self._project_win.refresh()
        self._rx_model.refresh_symbols()
        self._tx_model.refresh_signals()
        self._main_tabs.setCurrentWidget(self._database_win)

    def _remove_project_file(self, path: str, category: str):
        """Remove a file from the project and unload it from active managers."""
        if category == "db":
            self._project.remove_database_file(path)
            try:
                self._db_manager.remove_file(path)
            except Exception:
                pass
            from pathlib import Path as _Path
            if _Path(path).suffix.lower() in ('.dbc', '.kcd'):
                self._database_win.remove_dbc(path)
            self._rx_model.refresh_symbols()
            self._tx_model.refresh_signals()
        elif category == "trace":
            self._project.remove_trace_file(path)
        elif category == "plot":
            self._project.remove_plot_file(path)
        elif category == "script":
            self._script_plugin.unload()
            self._dispatcher.set_rx_hook(None)
            self._project.set_script_plugin("")
        elif category == "seedkey":
            self._diag_win._security_loader.unload()
            self._project.set_seedkey_file("")
        self._project_win.refresh()

    # -- Project management --

    def _new_project(self):
        """Create a new blank project, clearing all models and resetting the title."""
        self._project.new()
        self._db_manager.clear()
        self._rx_model.clear()
        self._tx_model.clear()
        self._watch_model.clear()
        self._trace_model.clear()
        self._sync_trace_folder()
        self._rx_filter_model.from_dicts([])
        self._database_win.from_dict([])
        self._project_win.refresh()
        self.setWindowTitle("cangui - Untitled")

    def _open_project(self):
        """Open a file dialog and load the selected project file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "Project Files (*.json);;All Files (*)")
        if not path:
            return
        self.open_project(path)

    def open_project(self, path: str):
        """Open a project file by path (called from CLI or file dialog)."""
        try:
            self._project.load(path)
            self._restore_project_state()
            self._sync_trace_folder()
            self.setWindowTitle(f"cangui - {self._project.name}")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Open Error", f"Failed to open project:\n{e}")

    def _restore_project_state(self):
        """Restore application state from loaded project data."""
        data = self._project.data

        # Reload databases into db_manager and database view
        self._db_manager.clear()
        self._database_win.from_dict([])

        # Import database files into the decode manager; DBC/KCD also go to the view
        for db_file in data.database_files:
            try:
                self._db_manager.load_file(db_file)
            except Exception:
                pass
            from pathlib import Path as _Path
            if _Path(db_file).suffix.lower() in ('.dbc', '.kcd'):
                try:
                    self._database_win.import_dbc_silent(db_file)
                except Exception:
                    pass

        # Append manually created databases from .db.json
        db_editor_data = self._project.load_database_editor()
        if db_editor_data:
            self._database_win.append_from_dict(db_editor_data)

        self._project_win.refresh()
        self._rx_model.refresh_symbols()
        self._tx_model.refresh_signals()

        # Restore TX messages
        from cangui.model_tx_message import TxMessageItem
        self._tx_model.beginResetModel()
        self._tx_model._items.clear()
        self._tx_model.endResetModel()
        for tx in data.tx_messages:
            try:
                raw = bytearray.fromhex(tx.get("raw_data", ""))
            except ValueError:
                raw = bytearray(tx.get("length", 8))
            self._tx_model.add_message(TxMessageItem(
                bus=tx.get("bus", 1),
                can_id=tx.get("can_id", 0),
                is_extended_id=tx.get("is_extended_id", False),
                dlc=tx.get("dlc", 8),
                length=tx.get("length", 8),
                symbol=tx.get("symbol", ""),
                raw_data=raw,
                cycle_time_ms=tx.get("cycle_time_ms", 100),
                cycle_enabled=tx.get("cycle_enabled", False),
            ), resolve=False)

        # Restore watch signals
        self._watch_model.clear()
        for ws in data.watch_signals:
            self._watch_model.add_watch(
                arb_id=ws.get("arb_id", 0),
                signal_name=ws.get("signal_name", ""),
                display_name=ws.get("display_name", ""),
                unit=ws.get("unit", ""),
                direction=ws.get("direction", "Rx"),
            )

        # Restore watch DIDs
        self._watch_did_win._model.clear()
        for wd in data.watch_dids:
            self._watch_did_win._model.add_entry(
                did=wd.get("did", 0),
                name=wd.get("name", ""),
                cycle_ms=wd.get("cycle_ms", 500),
            )

        # Restore connections
        self._can_service.disconnect_all()
        # Remove existing connections in reverse order
        for i in range(len(self._can_service.connections) - 1, -1, -1):
            self._can_service.remove_connection(i)
        _socketcan_ifaces = {"socketcan-virtual", "socketcan"}
        for cd in data.connections:
            iface = cd.get("interface", "socketcan-virtual")
            channel = cd.get("channel", "vcan0")
            # Remap Linux-only SocketCAN interfaces to the cross-platform virtual bus
            if sys.platform == "win32" and iface in _socketcan_ifaces:
                iface = "virtual"
                channel = ""
            config = BusConfig(
                interface=iface,
                channel=channel,
                bitrate=cd.get("bitrate", 500000),
                fd=cd.get("fd", False),
                name=cd.get("name", ""),
                bus_number=cd.get("bus_number", 1),
            )
            self._can_service.add_connection(config)

        # Restore rx filters
        if hasattr(data, 'rx_filters') and data.rx_filters:
            self._rx_filter_model.from_dicts(data.rx_filters)

        # Restore settings
        if data.settings:
            self._settings_win.apply_project_settings(data.settings)
            script_api.set_log_throttle(self._options.script.log_throttle_s)

        # Restore Script plugin
        self._script_plugin.unload()
        self._dispatcher.set_rx_hook(None)
        if data.script_plugin_file:
            try:
                self._script_plugin.load(data.script_plugin_file)
                self._dispatcher.set_rx_hook(self._script_plugin.apply_rx)
            except Exception:
                pass

        # Restore seed-key plugin
        self._diag_win._security_loader.unload()
        if data.seedkey_file:
            try:
                self._diag_win._security_loader.load(data.seedkey_file)
            except Exception:
                pass

        # Restore workspace layout
        if data.workspace_state:
            self._workspace_service.restore_state(data.workspace_state)
            # Sync ratios from restored splitter sizes
            self._save_ratios(self._h_splitter, '_h_ratios')
            self._save_ratios(self._v_splitter, '_v_ratios')
            self._save_ratios(self._rx_tx_win.splitter, '_rxtx_ratios')

    def _save_project(self):
        """Save the current project to its existing path, or prompt for one if unsaved."""
        if self._project.path is None:
            self._save_project_as()
            return
        self._collect_project_state()
        self._project.save()
        self.setWindowTitle(f"cangui - {self._project.name}")
        self._sync_trace_folder()
        self._project_win.refresh()

    def _save_project_as(self):
        """Prompt the user for a save path and write the project to that location."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "", "Project Files (*.json);;All Files (*)")
        if path:
            if not path.endswith(".json"):
                path += ".json"
            self._collect_project_state()
            self._project.save(path)
            self.setWindowTitle(f"cangui - {self._project.name}")
            self._sync_trace_folder()
            self._project_win.refresh()

    def _collect_project_state(self):
        """Collect current application state into the project data."""
        data = self._project.data

        # TX messages
        data.tx_messages = []
        for item in self._tx_model.items:
            data.tx_messages.append({
                "bus": item.bus,
                "can_id": item.can_id,
                "is_extended_id": item.is_extended_id,
                "dlc": item.dlc,
                "length": item.length,
                "symbol": item.symbol,
                "raw_data": item.raw_data.hex(),
                "cycle_time_ms": item.cycle_time_ms,
                "cycle_enabled": item.cycle_enabled,
            })

        # Watch signals
        data.watch_signals = []
        for entry in self._watch_model.entries:
            data.watch_signals.append({
                "arb_id": entry.arb_id,
                "signal_name": entry.signal_name,
                "display_name": entry.display_name,
                "unit": entry.unit,
                "direction": entry.direction,
            })

        # Watch DIDs
        data.watch_dids = []
        for entry in self._watch_did_win._model.entries:
            data.watch_dids.append({
                "did": entry.did,
                "name": entry.name,
                "cycle_ms": entry.cycle_ms,
            })

        # Connection configs
        data.connections = []
        for conn in self._can_service.connections:
            data.connections.append({
                "interface": conn.config.interface,
                "channel": conn.config.channel,
                "bitrate": conn.config.bitrate,
                "fd": conn.config.fd,
                "name": conn.config.name,
                "bus_number": conn.config.bus_number,
            })

        # Rx filters
        data.rx_filters = self._rx_filter_model.to_dicts()

        # Workspace layout
        data.workspace_state = self._workspace_service.save_state()

        # Settings
        data.settings = self._settings_win.collect_settings()

        # Database editor — only save manually created databases
        db_data = self._database_win.manual_databases_to_dict()
        if db_data:
            self._project.save_database_editor(db_data)


    def _close_project(self):
        """Close the current project, clearing all models and resetting the window title."""
        self._project.new()
        self._db_manager.clear()
        self._rx_model.clear()
        self._tx_model.beginResetModel()
        self._tx_model._items.clear()
        self._tx_model.endResetModel()
        self._watch_model.clear()
        self._project_win.refresh()
        self.setWindowTitle("cangui")

    # -- Trace --

    def _trace_start(self):
        """Start trace recording, requiring an already-saved project.

        Logs an error and returns early if the project has not been saved yet.
        """
        if self._project.path is None:
            self._log_win.append(
                "ERROR",
                "Trace cannot start: project is not saved. Save the project first (Ctrl+S).")
            return
        self._trace_model.start()
        self._trace_win.set_recording_state(True)

    def _trace_stop(self):
        """Stop the active trace recording or replay (F6 shortcut handler)."""
        self._on_trace_stop_requested()
        self._trace_model.stop()
        self._trace_win.set_recording_state(False)

    def _sync_trace_folder(self):
        """Update the trace model's output folder from the current project path."""
        self._trace_model.set_trace_folder(self._project.trace_folder)

    def _update_replay_trace_info(self, ctb_path: str):
        """Update the Replay tab with trace file information.

        Args:
            ctb_path: Path to the loaded CTB file.
        """
        if not ctb_path:
            self._replay_list_win.set_file_info("")
            return

        reader = TraceStoreReader(ctb_path)
        frame_count = reader.row_count()
        duration = reader.duration()

        self._replay_list_win.set_file_info(ctb_path, frame_count, duration)

    def _on_trace_file_changed(self, path: str):
        """Update replay window with trace info when file changes.

        Args:
            path: Path to the newly loaded trace file.
        """
        self._update_replay_trace_info(path)
        """Called when a new trace file is opened. Add it to the project."""
        if path:
            self._project.add_trace_file(path)
            self._project_win.refresh()

    def _save_trace(self, path: str):
        """Export all captured trace rows to a trace file."""
        fmt = TraceFormat(detect_trace_format(path))
        writer = create_trace_writer(path, fmt)
        writer.open()
        for entry in self._trace_model.iter_all_entries():
            msg = CanMessage(
                arbitration_id=entry.can_id,
                data=entry.data,
                is_extended_id=entry.is_extended_id,
                is_fd=entry.frame_type == "FD",
                dlc=entry.dlc,
                timestamp=entry.timestamp,
                bus=entry.bus,
            )
            writer.write(msg, direction=entry.direction)
        writer.close()

    def _load_trace(self, path: str):
        """Load a trace file and display it in the Trace tab.

        Supports .ctb, .trc, and .blf file formats:
        - .ctb files are loaded directly into the Trace tab for browsing
        - .trc and .blf files are converted to .ctb format first, then loaded

        The conversion creates a .ctb file next to the original file (if it
        doesn't already exist) and automatically adds it to the project.

        Args:
            path: Absolute path to the trace file (.ctb, .trc, or .blf).
        """
        from pathlib import Path
        from cangui.model_trace import _convert_trc_to_ctb

        path_obj = Path(path)
        suffix = path_obj.suffix.lower()

        # Handle .ctb files directly
        if suffix == ".ctb":
            self._trace_model.load_trace_file(str(path_obj))
            return

        # Convert .trc and .blf files to .ctb format
        if suffix in (".trc", ".blf"):
            ctb_path = path_obj.with_suffix(".ctb")
            if not ctb_path.exists():
                print(f"Converting {path} to CTB format...")
                if not _convert_trc_to_ctb(path_obj, ctb_path):
                    print(f"Failed to convert {path} to CTB format")
                    return
            # Add the CTB file to the project
            self._project.add_trace_file(str(ctb_path))
            self._project_win.refresh()
            # Load the CTB file directly
            self._trace_model.load_trace_file(str(ctb_path))
            return

        # Fallback for unknown formats (shouldn't happen with current dialog filters)
        print(f"Unsupported trace file format: {suffix}")
        return

    def _on_replay_finished(self):
        """Stop the trace model when playback ends."""
        self._trace_model.stop()

    def _on_trace_stop_requested(self):
        """Handle Stop button or F6 shortcut for both recording and replay.

        Stops any active CTB replay in addition to model stop.
        """
        if self._ctb_player is not None:
            self._ctb_player.stop()
            self._ctb_player = None

    def _on_replay_requested(self):
        """Start CTB trace replay with current window settings.

        Reads the current CTB file from the trace model, configures the player
        with UI settings (mode, speed, bus, loop), connects signals, and starts
        playback.
        """
        from pathlib import Path

        ctb_path = self._trace_model.current_file
        if not ctb_path or not ctb_path.endswith(".ctb"):
            return

        reader = TraceStoreReader(ctb_path)
        if reader.row_count() == 0:
            return

        # Stop any existing player
        if self._ctb_player is not None:
            self._ctb_player.stop()

        # Create new player
        self._ctb_player = CtbTracePlayer(reader, self)
        self._ctb_player.speed = self._replay_list_win.speed_factor
        self._ctb_player.mode = ReplayMode(self._replay_list_win.replay_mode)
        self._ctb_player.target_bus = self._replay_list_win.replay_bus
        self._ctb_player.loop = self._replay_list_win.replay_loop

        # Wire signals
        self._ctb_player.tx_frame.connect(self._send_message)
        self._ctb_player.rx_frame.connect(
            lambda msg, direction: (
                self._dispatcher.dispatch(msg),
                self._trace_model.on_message(msg, direction),
            )
        )
        self._ctb_player.progress_changed.connect(
            lambda t: self._replay_list_win.set_replay_progress(t, reader.duration())
        )
        self._ctb_player.finished_playback.connect(self._on_ctb_replay_finished)

        # Update UI and start playback
        self._replay_list_win.set_replay_state(True)
        self._ctb_player.start()

    def _on_ctb_replay_finished(self):
        """Handle completion of CTB trace replay."""
        self._ctb_player = None
        self._replay_list_win.set_replay_state(False)

    def _on_replay_pause_requested(self):
        """Pause the active CTB trace replay."""
        if self._ctb_player is not None:
            self._ctb_player.pause()

    def _on_replay_resume_requested(self):
        """Resume the paused CTB trace replay."""
        if self._ctb_player is not None:
            self._ctb_player.resume()

    def _on_replay_reset_requested(self):
        """Stop and restart the CTB trace replay from the beginning."""
        if self._ctb_player is not None:
            # Stop the current player
            self._ctb_player.stop()
            self._ctb_player = None
        # Restart replay with current settings
        self._on_replay_requested()

    # -- UDS / Diagnostics --

    def _uds_connect(self, tx_id: int, rx_id: int, bus_number: int = 0):
        """Connect UDS service using the specified CAN bus (or first connected)."""
        bus = self._get_raw_bus(bus_number)
        if bus is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "UDS Error",
                                "No matching CAN bus connected. Add a connection first.")
            return
        config = UdsConfig(tx_id=tx_id, rx_id=rx_id)
        self._uds_service.connect(bus, config)

    def _uds_disconnect(self):
        """Disconnect the active UDS session."""
        self._uds_service.disconnect()

    def _get_raw_bus(self, bus_number: int = 0):
        """Get the underlying python-can bus matching the bus number.

        If bus_number is 0 or no match is found, falls back to first connected bus.
        """
        if bus_number > 0:
            for conn in self._can_service.connections:
                if conn.bus.is_connected and conn.config.bus_number == bus_number:
                    return conn.bus._bus
        # Fallback: first connected bus
        for conn in self._can_service.connections:
            if conn.bus.is_connected:
                return conn.bus._bus
        return None

    # -- Watch --

    def _add_signal_to_watch(self, arb_id: int, signal_name: str, unit: str, direction: str):
        """Forward a signal addition request to the WatchModel.

        Args:
            arb_id: CAN arbitration ID of the source message.
            signal_name: DBC signal name.
            unit: Physical unit string.
            direction: ``"Rx"``, ``"Tx"``, or ``"Db"``.
        """
        self._watch_model.add_watch(arb_id, signal_name, unit=unit, direction=direction)

    # -- Plot --

    def _on_plot_record_toggled(self, recording: bool):
        """Start or stop plot data recording and the companion plot trace file.

        When starting, requires a saved project (project path must be set).
        On start, :class:`~cangui.service_plot_data.PlotDataService` and the
        plot trace service are both activated.  On stop, both are halted.

        Args:
            recording: ``True`` to start recording, ``False`` to stop.
        """
        if recording:
            if self._project.path is None:
                self._log_win.append(
                    "ERROR",
                    "Plot recording cannot start: project is not saved. Save first (Ctrl+S).")
                self._plot_win.set_recording_state(False)
                return
            self._plot_service.start()
            self._plot_trace_service.set_trace_folder(self._project.plot_folder)
            self._plot_trace_service.set_trace_format(self._options.tracer.trace_format)
            self._plot_trace_service.start()
        else:
            self._plot_service.stop()
            self._plot_trace_service.stop()
        self._plot_recording = recording
        self._plot_win.set_recording_state(recording)

    def _auto_start_plot(self):
        """Start plot recording automatically when the first signal is added."""
        if not self._plot_recording:
            self._on_plot_record_toggled(True)

    def _on_plot_trace_file_changed(self, path: str):
        """Add a newly created plot trace file to the project and refresh the project view.

        Args:
            path: Absolute path to the new plot trace file.
        """
        if path:
            self._project.add_plot_file(path)
            self._project_win.refresh()

    def _add_signal_to_plot(self, arb_id: int, signal_name: str, unit: str):
        """Forward a signal addition request to the PlotListWindow.

        Args:
            arb_id: CAN arbitration ID of the source message.
            signal_name: DBC signal name.
            unit: Physical unit string.
        """
        self._plot_list_win.add_signal(arb_id, signal_name, unit)

    # -- Script / Seed-Key plugins --

    def _on_open_in_database(self, path: str):
        """Switch to the Database tab when the user opens a file from the Project view.

        Args:
            path: Absolute path to the database file to show.
        """
        self._main_tabs.setCurrentWidget(self._database_win)

    def _on_open_trace_from_project(self, path: str):
        """Switch to the Trace tab when a trace file is opened in Project view.

        Args:
            path: Absolute path to the trace file node selected by the user.
                The current behavior is tab navigation only.
        """
        _ = path
        self._main_tabs.setCurrentWidget(self._trace_win)
        view = getattr(self._database_win, "primary_view", None)
        if view is not None:
            view.setFocus()

    def _on_import_file(self, path: str):
        """Dispatch a Project-view import by file extension."""
        if path.endswith('.script.py'):
            self._attach_script_plugin(path)
        elif path.endswith('.seedkey.py'):
            self._attach_seedkey(path)
        elif path.endswith('.dbc'):
            try:
                self._database_win.import_dbc(path)
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Import Error", f"Failed to import DBC:\n{e}")
        elif path.endswith('.db.json'):
            self._database_win.import_from_json(path)
        else:
            from pathlib import Path as _Path
            if _Path(path).suffix.lower() in ('.odx', '.pdx', '.odx-d'):
                self._attach_odx(path)

    def _attach_odx(self, path: str):
        """Load an ODX/PDX diagnostic database and register it in the project.

        Args:
            path: Absolute path to the ODX or PDX file to load.
        """
        try:
            self._db_manager.load_file(path)
            self._project.add_database_file(path)
            self._project_win.refresh()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "ODX Import Error", f"Failed to import ODX:\n{e}")

    def _attach_script_plugin(self, path: str):
        """Load a Python script plugin, wire it as the RX hook, and save it in the project.

        Args:
            path: Absolute path to the ``.script.py`` plugin file.
        """
        try:
            self._script_plugin.load(path)
            self._project.set_script_plugin(path)
            self._dispatcher.set_rx_hook(self._script_plugin.apply_rx)
            self._project_win.refresh()
            self._call_plugin_inits()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Script Plugin Error", f"Failed to load plugin:\n{e}")

    def _attach_seedkey(self, path: str):
        """Load a seed-key plugin into the Diagnostic window and save it in the project.

        Args:
            path: Absolute path to the ``.seedkey.py`` plugin file.
        """
        try:
            self._diag_win._security_loader.load(path)
            self._project.set_seedkey_file(path)
            self._project_win.refresh()
            self._call_plugin_inits()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Seed-Key Error", f"Failed to load plugin:\n{e}")

    # -- Settings --

    def _apply_tab_visibility(self):
        """Show or hide tabs according to current AppOptions.tabs settings."""
        t = self._options.tabs
        for widget, tab_widget, visible in [
            (self._rx_tx_win,    self._main_tabs,  t.receive_transmit),
            (self._database_win, self._main_tabs,  t.database),
            (self._trace_win,    self._main_tabs,  t.trace),
            (self._plot_win,     self._main_tabs,  t.plot),
            (self._diag_win,     self._main_tabs,  t.diagnostics),
            (self._project_win,  self._small_tabs, t.project_manager),
            (self._log_win,      self._small_tabs, t.log),
            (self._watch_win,    self._list_tabs,  t.watch),
            (self._watch_did_win,self._list_tabs,  t.watch_did),
            (self._dtc_win,      self._list_tabs,  t.dtc),
            (self._rx_filter_win,self._list_tabs,  t.rx_filter),
            (self._plot_list_win,self._list_tabs,  t.plot_list),
            (self._settings_win, self._list_tabs,  t.settings),
            (self._help_win,     self._list_tabs,  t.help),
        ]:
            idx = tab_widget.indexOf(widget)
            if idx >= 0:
                tab_widget.setTabVisible(idx, visible)

    def _on_setting_changed(self, category: str, key: str, value):
        """Handle a setting change from the Settings window."""
        if category == "plot" and key == "time_window":
            self._plot_service.time_window = float(value)
        elif category == "plot" and key == "update_interval_ms":
            self._plot_win.set_update_interval(int(value))
        elif category == "plot" and key == "max_display_points":
            self._plot_service.max_display_points = int(value)
        elif category == "tracer" and key == "trace_format":
            self._trace_model.set_trace_format(str(value))
        elif category == "general" and key == "decimal_places":
            from cangui import signal_decoder
            signal_decoder.set_decimal_places(int(value))
        elif category == "tabs":
            self._apply_tab_visibility()
        elif category == "script" and key == "log_throttle_s":
            script_api.set_log_throttle(float(value))

    # -- Misc --

    def _toggle_fullscreen(self):
        """Toggle between fullscreen and normal window mode."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _start_vcan(self):
        """Create and bring up the ``vcan0`` virtual CAN interface via sudo.

        Shows a warning dialog if the modprobe or ip commands fail.
        """
        import subprocess
        try:
            subprocess.run(["sudo", "modprobe", "vcan"], check=True)
            subprocess.run(["sudo", "ip", "link", "add", "vcan0", "type", "vcan"],
                           check=False)  # May already exist
            subprocess.run(["sudo", "ip", "link", "set", "up", "vcan0"], check=True)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "vcan Error", f"Failed to start vcan0:\n{e}")

    # -- Proportional splitter resizing --

    @staticmethod
    def _ratios_from_sizes(sizes: list[int]) -> list[float]:
        """Convert absolute pixel sizes to fractional ratios that sum to 1.0.

        Args:
            sizes: List of pane pixel sizes from :meth:`QSplitter.sizes`.

        Returns:
            List of proportional floats (one per pane).  If the total is zero,
            equal ratios are returned.
        """
        total = sum(sizes)
        if total == 0:
            return [1.0 / len(sizes)] * len(sizes)
        return [s / total for s in sizes]

    @staticmethod
    def _sizes_from_ratios(ratios: list[float], total: int) -> list[int]:
        """Convert fractional ratios to absolute pixel sizes for a given total width/height.

        Rounding remainder is assigned to the first pane so that sizes always
        sum exactly to *total*.

        Args:
            ratios: List of proportional floats (should sum to 1.0).
            total: Available pixel width or height to distribute.

        Returns:
            List of integer pixel sizes for use with :meth:`QSplitter.setSizes`.
        """
        raw = [int(r * total) for r in ratios]
        # Distribute rounding remainder to the first pane
        raw[0] += total - sum(raw)
        return raw

    def _save_ratios(self, splitter, attr: str):
        """Snapshot the current splitter proportions into an instance attribute.

        Args:
            splitter: :class:`QSplitter` whose current sizes should be captured.
            attr: Name of the instance attribute (e.g. ``"_h_ratios"``) to update.
        """
        sizes = splitter.sizes()
        if sum(sizes) > 0:
            setattr(self, attr, self._ratios_from_sizes(sizes))

    def _apply_ratios(self):
        """Recompute and apply absolute splitter sizes from the stored ratios.

        Called from :meth:`showEvent` and :meth:`resizeEvent` to maintain
        proportional pane sizes across window resize events.
        """
        w = self._h_splitter.width()
        h = self._v_splitter.height()
        rxtx_h = self._rx_tx_win.splitter.height()
        if w > 0:
            self._h_splitter.setSizes(self._sizes_from_ratios(self._h_ratios, w))
        if h > 0:
            self._v_splitter.setSizes(self._sizes_from_ratios(self._v_ratios, h))
        if rxtx_h > 0:
            self._rx_tx_win.splitter.setSizes(
                self._sizes_from_ratios(self._rxtx_ratios, rxtx_h))

    def showEvent(self, event):
        """Apply stored splitter ratios on first show.

        Args:
            event: Qt show event passed to the parent class.
        """
        super().showEvent(event)
        self._apply_ratios()

    def resizeEvent(self, event):
        """Reapply splitter ratios after any window resize.

        Deferred via a zero-millisecond timer so the new geometry is fully
        committed before sizes are recalculated.

        Args:
            event: Qt resize event passed to the parent class.
        """
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_ratios)

    def closeEvent(self, event):
        """Stop all background workers and disconnect CAN buses before closing.

        Ensures the trace player, transmitter, UDS service, all CAN
        connections, and the trace DiskWriter are cleanly shut down before
        the window is destroyed.

        Args:
            event: Qt close event passed to the parent class.
        """
        if self._ctb_player is not None:
            self._ctb_player.stop()
        if self._transmitter is not None:
            self._transmitter.stop()
        self._uds_service.disconnect()
        self._can_service.disconnect_all()
        self._trace_model.shutdown()
        super().closeEvent(event)
