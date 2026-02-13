from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QSplitter, QTabWidget, QApplication,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import Qt

from cangui.core.can_bus import BusConfig
from cangui.core.can_message import CanMessage
from cangui.core.options import AppOptions
from cangui.core.project import Project
from cangui.core.database_manager import DatabaseManager
from cangui.core.signal_decoder import SignalDecoder
from cangui.core.trace_writer import TraceWriter
from cangui.core.trace_reader import TraceReader
from cangui.services.message_dispatcher import MessageDispatcher
from cangui.services.can_service import CanService
from cangui.services.plot_data_service import PlotDataService
from cangui.services.uds_service import UdsService
from cangui.core.uds_client import UdsConfig
from cangui.models.connection_model import ConnectionModel
from cangui.models.rx_message_model import RxMessageModel
from cangui.models.tx_message_model import TxMessageModel
from cangui.models.watch_model import WatchModel
from cangui.models.trace_model import TraceModel
from cangui.models.project_model import ProjectModel
from cangui.models.rx_filter_model import RxFilterModel
from cangui.ui.windows.rx_tx_window import RxTxWindow
from cangui.ui.windows.rx_filter_window import RxFilterWindow
from cangui.ui.windows.watch_window import WatchWindow
from cangui.ui.windows.project_window import ProjectWindow
from cangui.ui.windows.trace_window import TraceWindow
from cangui.ui.windows.plot_window import PlotWindow
from cangui.ui.windows.diagnostic_window import DiagnosticWindow
from cangui.ui.windows.watch_did_window import WatchDidWindow
from cangui.ui.windows.dtc_window import DtcWindow
from cangui.ui.windows.help_window import HelpWindow
from cangui.ui.dialogs.import_dbc_dialog import get_dbc_file_path
from cangui.ui.focus_manager import FocusManager
from cangui.services.workspace_service import WorkspaceService
from cangui.workers.can_transmitter import CanTransmitter
from cangui.workers.trace_player import TracePlayer


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
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
        self._uds_service = UdsService(self)

        # Models
        self._connection_model = ConnectionModel(self._can_service, self)
        self._rx_filter_model = RxFilterModel(self)
        self._rx_model = RxMessageModel(self._decoder, self._rx_filter_model, self)
        self._tx_model = TxMessageModel(self)
        self._tx_model.set_decoder(self._decoder)
        self._watch_model = WatchModel(self._decoder, self)
        self._trace_model = TraceModel(self._decoder, self)
        self._project_model = ProjectModel(self._project, self)

        # Wire dispatcher — batch path (CAN receiver)
        self._dispatcher.messages_received.connect(self._rx_model.on_messages)
        self._dispatcher.messages_received.connect(self._watch_model.on_messages)
        self._dispatcher.messages_received.connect(self._trace_model.on_messages)
        self._dispatcher.messages_received.connect(self._plot_service.on_messages)
        # Single-message path (trace player)
        self._dispatcher.message_received.connect(self._rx_model.on_message)
        self._dispatcher.message_received.connect(self._watch_model.on_message)
        self._dispatcher.message_received.connect(self._trace_model.on_message)
        self._dispatcher.message_received.connect(self._plot_service.on_message)

        # TX transmitter (started when first connection is made)
        self._transmitter: CanTransmitter | None = None

        # Trace replay
        self._trace_player: TracePlayer | None = None

        # Create windows and layout
        self._create_layout()

        # Keyboard shortcuts (no menu bar)
        self._create_shortcuts()

        # Focus manager
        self._focus = FocusManager(self)
        self._focus.register("1", self._rx_tx_win, self._main_tabs, "Receive/Transmit")
        self._focus.register("2", self._trace_win, self._main_tabs, "Trace")
        self._focus.register("3", self._plot_win, self._main_tabs, "Plot")
        self._focus.register("4", self._diag_win, self._main_tabs, "Diagnostics")
        self._focus.register("5", self._project_win, self._small_tabs, "Project Manager")
        self._focus.register("6", self._watch_win, self._list_tabs, "Watch")
        self._focus.register("7", self._watch_did_win, self._list_tabs, "Watch DID")
        self._focus.register("8", self._dtc_win, self._list_tabs, "DTC")
        self._focus.register("9", self._rx_filter_win, self._list_tabs, "Rx Filter")
        self._focus.register("0", self._help_win, self._list_tabs, "Help")
        self._focus.install()

        # Populate help entries
        self._help_win.set_entries([
            ("1", "Receive/Transmit", "Window switch"),
            ("2", "Trace", "Window switch"),
            ("3", "Plot", "Window switch"),
            ("4", "Diagnostics", "Window switch"),
            ("5", "Project Manager", "Window switch"),
            ("6", "Watch", "Window switch"),
            ("7", "Watch DID", "Window switch"),
            ("8", "DTC", "Window switch"),
            ("9", "Rx Filter", "Window switch"),
            ("0 / F1", "Help", "Window switch"),
            ("Space", "Expand/collapse tree item", "Tree views"),
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

        # Focus stylesheet
        self.setStyleSheet(self.styleSheet() + """
            QTreeView[focused="true"]::item:selected,
            QTableView[focused="true"]::item:selected {
                background-color: #308CC6; color: white;
            }
            QTreeView[focused="false"]::item:selected,
            QTableView[focused="false"]::item:selected {
                background-color: #D0D0D0; color: #606060;
            }
        """)

    def _create_layout(self):
        # Create windows
        self._rx_tx_win = RxTxWindow(
            self._rx_model, self._tx_model, self._connection_model)
        self._rx_tx_win.add_tx_requested.connect(self._add_tx_frame)
        self._rx_tx_win.add_to_watch_requested.connect(self._add_signal_to_watch)
        self._rx_tx_win.add_to_plot_requested.connect(self._add_signal_to_plot)
        self._rx_tx_win.add_connection_requested.connect(self._add_connection)
        self._rx_tx_win.reset_connections_requested.connect(self._can_service.reset)
        self._rx_tx_win.set_send_once_callback(self._send_message)

        self._diag_win = DiagnosticWindow(self._uds_service)
        self._diag_win.connect_requested.connect(self._uds_connect)
        self._diag_win.disconnect_requested.connect(self._uds_disconnect)

        self._plot_win = PlotWindow(self._plot_service, self._db_manager)

        self._trace_win = TraceWindow(self._trace_model)
        self._trace_win.save_trace_requested.connect(self._save_trace)
        self._trace_win.load_trace_requested.connect(self._load_trace)
        self._trace_model.file_changed.connect(self._on_trace_file_changed)

        self._project_win = ProjectWindow(self._project_model)
        self._project_win.add_file_requested.connect(self._import_dbc)
        self._project_win.remove_file_requested.connect(self._remove_dbc)
        self._project_win.new_requested.connect(self._new_project)
        self._project_win.load_requested.connect(self._open_project)
        self._project_win.save_requested.connect(self._save_project)
        self._project_win.save_as_requested.connect(self._save_project_as)

        self._watch_win = WatchWindow(self._watch_model)
        self._watch_win.add_to_plot_requested.connect(self._add_signal_to_plot)

        self._watch_did_win = WatchDidWindow(self._uds_service)
        self._watch_did_win.add_to_plot_requested.connect(self._add_signal_to_plot)

        self._dtc_win = DtcWindow(self._uds_service)

        self._rx_filter_win = RxFilterWindow(self._rx_filter_model)

        # 3-pane layout with QSplitter + QTabWidget
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Main pane (left) — tabs
        self._main_tabs = QTabWidget()
        self._main_tabs.setTabsClosable(False)
        self._main_tabs.setMovable(True)
        self._main_tabs.addTab(self._rx_tx_win, "Receive/Transmit [1]")
        self._main_tabs.addTab(self._trace_win, "Trace [2]")
        self._main_tabs.addTab(self._plot_win, "Plot [3]")
        self._main_tabs.addTab(self._diag_win, "Diagnostics [4]")
        self._h_splitter.addWidget(self._main_tabs)

        # Right pane — vertical splitter
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Small pane (top-right)
        self._small_tabs = QTabWidget()
        self._small_tabs.setTabsClosable(False)
        self._small_tabs.setMovable(True)
        self._small_tabs.addTab(self._project_win, "Project Manager [5]")
        self._v_splitter.addWidget(self._small_tabs)

        # List pane (bottom-right)
        self._list_tabs = QTabWidget()
        self._list_tabs.setTabsClosable(False)
        self._list_tabs.setMovable(True)
        self._list_tabs.addTab(self._watch_win, "Watch [6]")
        self._list_tabs.addTab(self._watch_did_win, "Watch DID [7]")
        self._list_tabs.addTab(self._dtc_win, "DTC [8]")
        self._list_tabs.addTab(self._rx_filter_win, "Rx Filter [9]")

        self._help_win = HelpWindow()
        self._list_tabs.addTab(self._help_win, "Help [0]")

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

    def _create_shortcuts(self):
        def _shortcut(key, slot):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(slot)
            return s

        # File
        _shortcut("Ctrl+S", self._save_project)
        _shortcut("Shift+Ctrl+S", self._save_project)

        # View — window switching
        _shortcut("Alt+1", lambda: self._focus.activate(4))   # Project Manager
        _shortcut("Alt+2", lambda: self._focus.activate(1))   # Trace
        _shortcut("Alt+3", lambda: self._focus.activate(2))   # Plot
        _shortcut("Alt+4", lambda: self._focus.activate(5))   # Watch
        _shortcut("Alt+5", lambda: self._focus.activate(8))   # Rx Filter
        _shortcut("Alt+6", lambda: self._focus.activate(3))   # Diagnostics
        _shortcut("Alt+7", lambda: self._focus.activate(7))   # DTC
        _shortcut("Alt+8", lambda: self._focus.activate(6))   # Watch DID
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
        self._connection_model.add_empty_row()
        self._main_tabs.setCurrentWidget(self._rx_tx_win)

    def _on_connection_status(self, _index: int, status: str):
        if status == "OK":
            self._ensure_transmitter()

    def _ensure_transmitter(self):
        """Start the TX transmitter if not already running."""
        if self._transmitter is not None:
            return
        self._transmitter = CanTransmitter(self._tx_model, self._send_message, self)
        self._transmitter.counts_updated.connect(self._tx_model.increment_counts)
        self._transmitter.start()

    def _send_message(self, msg):
        """Send a CAN message on the first connected bus."""
        for conn in self._can_service.connections:
            if conn.bus.is_connected and conn.config.bus_number == msg.bus:
                conn.bus.send(msg)
                return
        # Fallback: send on first connected bus
        for conn in self._can_service.connections:
            if conn.bus.is_connected:
                conn.bus.send(msg)
                return

    # -- TX management --

    def _add_tx_frame(self):
        buses = [c.config.bus_number for c in self._can_service.connections] or [1]
        self._tx_model.add_empty_message(bus=buses[0])
        self._rx_tx_win.edit_last_tx_can_id()

    # -- DBC / Database management --

    def _import_dbc(self):
        path = get_dbc_file_path(self)
        if path:
            try:
                self._db_manager.load_file(path)
                self._project.add_database_file(path)
                self._project_win.refresh()
                self._rx_model.refresh_symbols()
                self._tx_model.refresh_signals()
                self._plot_win.refresh_signals()
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Import Error", f"Failed to load database:\n{e}")

    def _remove_dbc(self, path: str):
        self._db_manager.remove_file(path)
        self._project.remove_database_file(path)
        self._project_win.refresh()
        self._rx_model.refresh_symbols()
        self._tx_model.refresh_signals()
        self._plot_win.refresh_signals()

    # -- Project management --

    def _new_project(self):
        self._project.new()
        self._db_manager.clear()
        self._rx_model.clear()
        self._tx_model.clear()
        self._watch_model.clear()
        self._trace_model.clear()
        self._trace_model.set_trace_folder(None)
        self._rx_filter_model.from_dicts([])
        self._project_win.refresh()
        self.setWindowTitle("cangui - Untitled")

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "Project Files (*.json);;All Files (*)")
        if not path:
            return
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

        # Reload databases
        self._db_manager.clear()
        for db_file in data.database_files:
            try:
                self._db_manager.load_file(db_file)
            except Exception:
                pass
        self._project_win.refresh()
        self._rx_model.refresh_symbols()
        self._tx_model.refresh_signals()
        self._plot_win.refresh_signals()

        # Restore TX messages
        from cangui.models.tx_message_model import TxMessageItem
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
        for cd in data.connections:
            config = BusConfig(
                interface=cd.get("interface", "socketcan-virtual"),
                channel=cd.get("channel", "vcan0"),
                bitrate=cd.get("bitrate", 500000),
                fd=cd.get("fd", False),
                name=cd.get("name", ""),
                bus_number=cd.get("bus_number", 1),
            )
            self._can_service.add_connection(config)

        # Restore rx filters
        if hasattr(data, 'rx_filters') and data.rx_filters:
            self._rx_filter_model.from_dicts(data.rx_filters)

        # Restore workspace layout
        if data.workspace_state:
            self._workspace_service.restore_state(data.workspace_state)
            # Sync ratios from restored splitter sizes
            self._save_ratios(self._h_splitter, '_h_ratios')
            self._save_ratios(self._v_splitter, '_v_ratios')
            self._save_ratios(self._rx_tx_win.splitter, '_rxtx_ratios')

    def _save_project(self):
        if self._project.path is None:
            self._save_project_as()
            return
        self._collect_project_state()
        self._project.save()
        self.setWindowTitle(f"cangui - {self._project.name}")
        self._sync_trace_folder()
        self._project_win.refresh()

    def _save_project_as(self):
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

        # UDS config
        if self._uds_service.is_connected:
            cfg = self._uds_service.config
            data.uds_config = {
                "tx_id": cfg.tx_id,
                "rx_id": cfg.rx_id,
                "timeout": cfg.timeout,
            }

        # Rx filters
        data.rx_filters = self._rx_filter_model.to_dicts()

        # Workspace layout
        data.workspace_state = self._workspace_service.save_state()

    def _close_project(self):
        self._project.new()
        self._db_manager.clear()
        self._rx_model.clear()
        self._tx_model.beginResetModel()
        self._tx_model._items.clear()
        self._tx_model.endResetModel()
        self._watch_model.clear()
        self._project_win.refresh()
        self._plot_win.refresh_signals()
        self.setWindowTitle("cangui")

    # -- Trace --

    def _trace_start(self):
        self._trace_model.start()
        self._trace_win._on_start()

    def _trace_pause(self):
        self._trace_model.pause()
        self._trace_win._on_pause()

    def _trace_stop(self):
        self._trace_model.stop()
        self._trace_win._on_stop()

    def _sync_trace_folder(self):
        """Update the trace model's output folder from the current project path."""
        self._trace_model.set_trace_folder(self._project.trace_folder)

    def _on_trace_file_changed(self, path: str):
        """Called when a new trace file is opened. Add it to the project."""
        if path:
            self._project.add_trace_file(path)
            self._project_win.refresh()

    def _save_trace(self, path: str):
        """Export the current display buffer to a trace file."""
        self._trace_model.flush_all()
        writer = TraceWriter(path)
        writer.open()
        for entry in self._trace_model.entries:
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
        reader = TraceReader(path)
        reader.load()
        if not reader.entries:
            return
        # Stop any existing replay
        if self._trace_player is not None:
            self._trace_player.stop()
        self._trace_player = TracePlayer(reader, self)
        self._trace_player.message_played.connect(self._trace_model.on_message)
        self._trace_player.message_played.connect(
            lambda msg, _: self._dispatcher.dispatch(msg)
        )
        self._trace_player.finished_playback.connect(self._on_replay_finished)
        self._trace_model.clear()
        self._trace_model.start()
        self._trace_player.speed = self._trace_win.speed_factor
        self._trace_win.set_replay_state(True)
        self._trace_player.start()

    def _on_replay_finished(self):
        self._trace_model.stop()
        self._trace_win.set_replay_state(False)

    # -- UDS / Diagnostics --

    def _uds_connect(self, tx_id: int, rx_id: int):
        """Connect UDS service using the first connected CAN bus."""
        bus = self._get_raw_bus()
        if bus is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "UDS Error",
                                "No CAN bus connected. Add a connection first.")
            return
        config = UdsConfig(tx_id=tx_id, rx_id=rx_id)
        self._uds_service.connect(bus, config)

    def _uds_disconnect(self):
        self._uds_service.disconnect()

    def _get_raw_bus(self):
        """Get the underlying python-can bus from the first connected connection."""
        for conn in self._can_service.connections:
            if conn.bus.is_connected:
                return conn.bus._bus  # Access the raw can.Bus
        return None

    # -- Watch --

    def _add_signal_to_watch(self, arb_id: int, signal_name: str, unit: str, direction: str):
        self._watch_model.add_watch(arb_id, signal_name, unit=unit, direction=direction)

    # -- Plot --

    def _add_signal_to_plot(self, arb_id: int, signal_name: str, unit: str):
        self._plot_win._add_signal(arb_id, signal_name, unit)
        self._main_tabs.setCurrentWidget(self._plot_win)

    # -- Misc --

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _start_vcan(self):
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
        total = sum(sizes)
        if total == 0:
            return [1.0 / len(sizes)] * len(sizes)
        return [s / total for s in sizes]

    @staticmethod
    def _sizes_from_ratios(ratios: list[float], total: int) -> list[int]:
        raw = [int(r * total) for r in ratios]
        # Distribute rounding remainder to the first pane
        raw[0] += total - sum(raw)
        return raw

    def _save_ratios(self, splitter, attr: str):
        sizes = splitter.sizes()
        if sum(sizes) > 0:
            setattr(self, attr, self._ratios_from_sizes(sizes))

    def _apply_ratios(self):
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
        super().showEvent(event)
        self._apply_ratios()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_ratios()

    def closeEvent(self, event):
        if self._trace_player is not None:
            self._trace_player.stop()
        if self._transmitter is not None:
            self._transmitter.stop()
        self._uds_service.disconnect()
        self._can_service.disconnect_all()
        super().closeEvent(event)
