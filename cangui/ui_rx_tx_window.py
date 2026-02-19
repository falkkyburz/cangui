from PySide6.QtWidgets import (
    QApplication, QComboBox, QHeaderView, QTreeView, QSplitter,
    QStyledItemDelegate, QToolBar, QLabel, QWidget, QVBoxLayout,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, Signal, QEvent, QObject

from cangui.model_rx_message import RxMessageModel
from cangui.model_tx_message import TxMessageModel
from cangui.model_connection import ConnectionModel, InterfaceDelegate
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.ui_tab_navigation import TabTreeView
from cangui.icons import icon as _icon


class _ClickOutsideFilter(QObject):
    """App-level event filter: clears all view selections when a mouse press
    lands inside the RxTxWindow but outside the three tree views."""

    def __init__(self, window: QWidget, views: list, parent=None):
        super().__init__(parent)
        self._window = window
        self._views  = views

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            if target is not None and self._inside_window(target):
                if not any(self._inside_view(target, v) for v in self._views):
                    for v in self._views:
                        v.clearSelection()
        return False  # never consume the event

    def _inside_window(self, widget: QWidget) -> bool:
        return widget is self._window or self._window.isAncestorOf(widget)

    @staticmethod
    def _inside_view(widget: QWidget, view: QWidget) -> bool:
        return widget is view or view.isAncestorOf(widget)


class SymbolDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Symbol column in the TX view."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        model = index.model()
        if hasattr(model, "get_all_symbols"):
            symbols = model.get_all_symbols()
            combo.addItems(symbols)
        # Pre-select current value
        current = index.data(Qt.ItemDataRole.EditRole)
        if current:
            idx = combo.findText(str(current))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(str(current))
        return combo

    def setEditorData(self, editor, index):
        current = index.data(Qt.ItemDataRole.EditRole)
        if current:
            idx = editor.findText(str(current))
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                editor.setEditText(str(current))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

# RX/TX column widths: Bus, ID(hex), Ext, Type, Length, Symbol, Data(hex), ...
_DEFAULT_WIDTHS = [40, 80, 35, 50, 50, 120, 200, 80, 60, 60, 60]

# Connection table: (empty/checkbox), Bus, Name, Channel, Interface, Bit Rate,
#                   Status, Overruns, QXmtFulls, Options  (Bus Load stretches)
_DEFAULT_CONN_WIDTHS = [28, 38, 80, 110, 120, 80, 60, 68, 68, 48]


class RxTxWindow(BaseDockWindow):
    TITLE = "Receive / Transmit"

    add_tx_requested = Signal()
    add_to_watch_requested = Signal(int, str, str, str)  # arb_id, signal_name, unit, direction
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit
    add_connection_requested = Signal()
    reset_connections_requested = Signal()

    def __init__(self, rx_model: RxMessageModel, tx_model: TxMessageModel,
                 connection_model: ConnectionModel, parent=None):
        super().__init__(parent)
        self._rx_model = rx_model
        self._tx_model = tx_model
        self._connection_model = connection_model

        # RX Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        rx_title = QLabel("  RX  ")
        rx_title.setStyleSheet("font-weight: bold; font-size: 15px; letter-spacing: 1px;")
        toolbar.addWidget(rx_title)
        toolbar.addSeparator()

        clear_action = QAction(_icon("trash"), "Clear", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        add_rx_watch_action = QAction(_icon("watch"), "Add to Watch", self)
        add_rx_watch_action.triggered.connect(self._add_rx_to_watch)
        toolbar.addAction(add_rx_watch_action)

        add_rx_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        add_rx_plot_action.triggered.connect(self._add_rx_to_plot)
        toolbar.addAction(add_rx_plot_action)

        self._layout.addWidget(toolbar)

        # Main splitter: RX (top), TX (middle), Connections (bottom)
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        # RX section
        rx_container = QWidget()
        rx_layout = QVBoxLayout(rx_container)
        rx_layout.setContentsMargins(0, 0, 0, 0)
        rx_layout.setSpacing(0)

        self._rx_view = TabTreeView()
        self._rx_view.setRootIsDecorated(True)
        self._rx_view.setAlternatingRowColors(True)
        self._rx_view.setModel(self._rx_model)
        self._rx_view.header().setStretchLastSection(True)
        self._rx_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._rx_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._rx_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._rx_view.customContextMenuRequested.connect(self._rx_context_menu)
        self._set_default_widths(self._rx_view)
        rx_layout.addWidget(self._rx_view)
        self._splitter.addWidget(rx_container)

        # TX section with toolbar + table
        tx_container = QWidget()
        tx_layout = QVBoxLayout(tx_container)
        tx_layout.setContentsMargins(0, 0, 0, 0)
        tx_layout.setSpacing(0)

        tx_toolbar = QToolBar()
        tx_toolbar.setMovable(False)

        tx_title = QLabel("  TX  ")
        tx_title.setStyleSheet("font-weight: bold; font-size: 15px; letter-spacing: 1px;")
        tx_toolbar.addWidget(tx_title)
        tx_toolbar.addSeparator()

        add_tx_action = QAction(_icon("add"), "Add Frame", self)
        add_tx_action.triggered.connect(self.add_tx_requested)
        tx_toolbar.addAction(add_tx_action)

        remove_tx_action = QAction(_icon("remove"), "Remove", self)
        remove_tx_action.triggered.connect(self._remove_tx)
        tx_toolbar.addAction(remove_tx_action)

        duplicate_tx_action = QAction(_icon("copy"), "Duplicate", self)
        duplicate_tx_action.triggered.connect(self._duplicate_tx)
        tx_toolbar.addAction(duplicate_tx_action)

        send_once_action = QAction(_icon("send"), "Send Once", self)
        send_once_action.triggered.connect(self._send_once)
        tx_toolbar.addAction(send_once_action)

        clear_counters_action = QAction(_icon("clear"), "Clear Counters", self)
        clear_counters_action.triggered.connect(self._clear_tx_counters)
        tx_toolbar.addAction(clear_counters_action)

        tx_toolbar.addSeparator()

        up_tx_action = QAction(_icon("up"), "Move Up", self)
        up_tx_action.triggered.connect(self._move_tx_up)
        tx_toolbar.addAction(up_tx_action)

        down_tx_action = QAction(_icon("down"), "Move Down", self)
        down_tx_action.triggered.connect(self._move_tx_down)
        tx_toolbar.addAction(down_tx_action)

        tx_toolbar.addSeparator()

        add_tx_watch_action = QAction(_icon("watch"), "Add to Watch", self)
        add_tx_watch_action.triggered.connect(self._add_tx_to_watch)
        tx_toolbar.addAction(add_tx_watch_action)

        add_tx_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        add_tx_plot_action.triggered.connect(self._add_tx_to_plot)
        tx_toolbar.addAction(add_tx_plot_action)

        tx_layout.addWidget(tx_toolbar)

        self._tx_view = TabTreeView()
        self._tx_view.setRootIsDecorated(True)
        self._tx_view.setAlternatingRowColors(True)
        self._tx_view.setModel(self._tx_model)
        self._tx_view.header().setStretchLastSection(True)
        self._tx_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._tx_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._tx_view.setItemDelegateForColumn(5, SymbolDelegate(self._tx_view))
        self._set_default_widths(self._tx_view)
        tx_layout.addWidget(self._tx_view)
        self._splitter.addWidget(tx_container)

        # Connections section
        conn_container = QWidget()
        conn_layout = QVBoxLayout(conn_container)
        conn_layout.setContentsMargins(0, 0, 0, 0)
        conn_layout.setSpacing(0)

        conn_toolbar = QToolBar()
        conn_toolbar.setMovable(False)

        conn_title = QLabel("  Connections  ")
        conn_title.setStyleSheet("font-weight: bold; font-size: 15px; letter-spacing: 1px;")
        conn_toolbar.addWidget(conn_title)
        conn_toolbar.addSeparator()

        add_conn_action = QAction(_icon("add"), "Add", self)
        add_conn_action.triggered.connect(self.add_connection_requested)
        conn_toolbar.addAction(add_conn_action)

        remove_conn_action = QAction(_icon("remove"), "Remove", self)
        remove_conn_action.triggered.connect(self._remove_connection)
        conn_toolbar.addAction(remove_conn_action)

        reset_conn_action = QAction(_icon("refresh"), "Reset", self)
        reset_conn_action.triggered.connect(self.reset_connections_requested)
        conn_toolbar.addAction(reset_conn_action)

        conn_layout.addWidget(conn_toolbar)

        self._conn_view = TabTreeView()
        self._conn_view.setRootIsDecorated(False)
        self._conn_view.setAlternatingRowColors(True)
        self._conn_view.setModel(self._connection_model)
        self._conn_view.setItemDelegateForColumn(4, InterfaceDelegate(self._conn_view))
        self._conn_view.header().setStretchLastSection(True)
        self._conn_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._set_default_widths(self._conn_view, _DEFAULT_CONN_WIDTHS)
        conn_layout.addWidget(self._conn_view)
        self._splitter.addWidget(conn_container)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setStretchFactor(2, 1)

        self._layout.addWidget(self._splitter)

        # Exclusive selection: selecting in one pane clears the other two
        self._rx_view.selectionModel().selectionChanged.connect(self._on_rx_selected)
        self._tx_view.selectionModel().selectionChanged.connect(self._on_tx_selected)
        self._conn_view.selectionModel().selectionChanged.connect(self._on_conn_selected)

        # Clear all selections on any click outside the three views
        self._click_filter = _ClickOutsideFilter(
            self, [self._rx_view, self._tx_view, self._conn_view], self)
        QApplication.instance().installEventFilter(self._click_filter)

    @property
    def splitter(self) -> QSplitter:
        return self._splitter

    @property
    def primary_view(self):
        return self._rx_view

    @property
    def selectable_views(self):
        return [self._rx_view, self._tx_view, self._conn_view]

    @staticmethod
    def _set_default_widths(view: QTreeView, widths: list[int] = _DEFAULT_WIDTHS):
        header = view.header()
        for i, width in enumerate(widths):
            if i < header.count():
                header.resizeSection(i, width)

    def edit_last_tx_can_id(self):
        """Start editing the CAN-ID cell of the last TX row."""
        last_row = self._tx_model.rowCount() - 1
        if last_row < 0:
            return
        idx = self._tx_model.index(last_row, 1)  # column 1 = CAN-ID
        self._tx_view.setCurrentIndex(idx)
        self._tx_view.scrollTo(idx)
        self._tx_view.edit(idx)

    def set_send_once_callback(self, callback):
        self._send_once_callback = callback

    # -- Exclusive selection across the three sub-views --

    def _on_rx_selected(self, selected, _):
        if selected.indexes():
            self._tx_view.clearSelection()
            self._conn_view.clearSelection()

    def _on_tx_selected(self, selected, _):
        if selected.indexes():
            self._rx_view.clearSelection()
            self._conn_view.clearSelection()

    def _on_conn_selected(self, selected, _):
        if selected.indexes():
            self._rx_view.clearSelection()
            self._tx_view.clearSelection()

    def _on_clear(self):
        self._rx_model.clear()

    def _remove_tx(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        if index.parent().isValid():
            row = index.parent().row()
        else:
            row = index.row()
        self._tx_model.remove_message(row)

    def _clear_tx_counters(self):
        self._tx_model.clear_counts()

    def _move_tx_up(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        row = index.parent().row() if index.parent().isValid() else index.row()
        self._tx_model.move_up(row)
        self._tx_view.setCurrentIndex(self._tx_model.index(row - 1, 0))

    def _move_tx_down(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        row = index.parent().row() if index.parent().isValid() else index.row()
        self._tx_model.move_down(row)
        self._tx_view.setCurrentIndex(self._tx_model.index(row + 1, 0))

    def _duplicate_tx(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        if index.parent().isValid():
            row = index.parent().row()
        else:
            row = index.row()
        item = self._tx_model.get_item(row)
        if item:
            from copy import deepcopy
            clone = deepcopy(item)
            clone.count = 0
            clone.cycle_enabled = False
            self._tx_model.add_message(clone)

    def _remove_connection(self):
        index = self._conn_view.currentIndex()
        if index.isValid():
            self._connection_model.remove_row(index.row())

    def _send_once(self):
        index = self._tx_view.currentIndex()
        if not index.isValid() or not hasattr(self, "_send_once_callback"):
            return
        if index.parent().isValid():
            row = index.parent().row()
        else:
            row = index.row()
        item = self._tx_model.get_item(row)
        if item:
            from cangui.can_message import CanMessage
            msg = CanMessage(
                arbitration_id=item.can_id,
                data=bytes(item.raw_data),
                is_extended_id=item.is_extended_id,
                dlc=item.length,
                bus=item.bus,
                row=row,
            )
            self._send_once_callback(msg)
            self._tx_model.increment_count(row)

    def _rx_context_menu(self, pos):
        index = self._rx_view.indexAt(pos)
        if not index.isValid():
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)

        result = self._rx_model.get_signal_at(index)
        if result is not None:
            item, sig = result
            watch_action = menu.addAction(f"Add '{sig.name}' to Watch")
            watch_action.triggered.connect(
                lambda: self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")
            )
            plot_action = menu.addAction(f"Add '{sig.name}' to Plot")
            plot_action.triggered.connect(
                lambda: self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
            )
        else:
            item = self._rx_model.get_item(index)
            if item and item.signals:
                action = menu.addAction("Add All Signals to Watch")
                action.triggered.connect(lambda: self._add_all_rx_to_watch(item))
                plot_action = menu.addAction("Add All Signals to Plot")
                plot_action.triggered.connect(lambda: self._add_all_rx_to_plot(item))

        if not menu.isEmpty():
            menu.exec(self._rx_view.viewport().mapToGlobal(pos))

    def _add_rx_to_watch(self):
        index = self._rx_view.currentIndex()
        if not index.isValid():
            return
        result = self._rx_model.get_signal_at(index)
        if result is not None:
            item, sig = result
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")
        else:
            item = self._rx_model.get_item(index)
            if item and item.signals:
                self._add_all_rx_to_watch(item)

    def _add_rx_to_plot(self):
        index = self._rx_view.currentIndex()
        if not index.isValid():
            return
        result = self._rx_model.get_signal_at(index)
        if result is not None:
            item, sig = result
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
        else:
            item = self._rx_model.get_item(index)
            if item and item.signals:
                self._add_all_rx_to_plot(item)

    def _add_tx_to_watch(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        result = self._tx_model.get_signal_at(index)
        if result is not None:
            item, sig = result
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Tx")
        else:
            item = self._tx_model.get_item_at(index)
            if item and item.signals:
                for sig in item.signals:
                    self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Tx")

    def _add_tx_to_plot(self):
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        result = self._tx_model.get_signal_at(index)
        if result is not None:
            item, sig = result
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
        else:
            item = self._tx_model.get_item_at(index)
            if item and item.signals:
                for sig in item.signals:
                    self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)

    def _add_all_rx_to_watch(self, item):
        for sig in item.signals:
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")

    def _add_all_rx_to_plot(self, item):
        for sig in item.signals:
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
