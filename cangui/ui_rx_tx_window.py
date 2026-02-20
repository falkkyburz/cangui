"""Receive/Transmit window module for cangui.

Provides :class:`RxTxWindow`, the main RX/TX panel arranged in a vertical
three-pane splitter (received messages, transmit messages, CAN connections).
Helper classes :class:`_ClickOutsideFilter` and :class:`SymbolDelegate` are
defined here as well.
"""
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHeaderView, QTreeView, QSplitter,
    QStyledItemDelegate, QToolBar, QLabel, QWidget, QVBoxLayout,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, Signal, QEvent, QObject, QSortFilterProxyModel

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
        """Initialise the filter and record the window and views to monitor.

        Args:
            window: The top-level widget that defines the "inside" boundary.
            views: List of QTreeView (or similar) widgets whose interiors
                should *not* trigger a selection clear.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._window = window
        self._views  = views

    def eventFilter(self, obj, event) -> bool:
        """Intercept application-level events and clear view selections.

        Triggered by the Qt event loop for every event dispatched while this
        filter is installed on ``QApplication``.  On a ``MouseButtonPress``
        that lands inside the monitored window but outside all registered
        views, every view's selection is cleared.

        Args:
            obj: The object that would normally receive the event (unused).
            event: The intercepted QEvent.

        Returns:
            ``False`` — the event is never consumed and always propagates.
        """
        if event.type() == QEvent.Type.MouseButtonPress:
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            if target is not None and self._inside_window(target):
                if not any(self._inside_view(target, v) for v in self._views):
                    for v in self._views:
                        v.clearSelection()
        return False  # never consume the event

    def _inside_window(self, widget: QWidget) -> bool:
        """Return True if widget is the monitored window or a descendant of it.

        Args:
            widget: Widget to test.

        Returns:
            ``True`` when *widget* is within the monitored window hierarchy.
        """
        return widget is self._window or self._window.isAncestorOf(widget)

    @staticmethod
    def _inside_view(widget: QWidget, view: QWidget) -> bool:
        """Return True if widget is the given view or a descendant of it.

        Args:
            widget: Widget to test.
            view: The tree view whose hierarchy is checked.

        Returns:
            ``True`` when *widget* is the view itself or nested inside it.
        """
        return widget is view or view.isAncestorOf(widget)


class SymbolDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Symbol column in the TX view."""

    def createEditor(self, parent, option, index):
        """Create a QComboBox editor pre-populated with all known symbols.

        Called by Qt when the user starts editing the Symbol column cell.
        If the underlying model exposes ``get_all_symbols()``, those symbols
        are loaded into the combo box.  The current cell value is
        pre-selected when it matches an existing entry, otherwise it is set
        as free-form text.

        Args:
            parent: Parent widget for the editor.
            option: Style option (unused).
            index: Model index of the cell being edited.

        Returns:
            A ``QComboBox`` configured with ``NoInsert`` policy and
            ``editable=True``.
        """
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
        """Synchronise the combo box selection with the current model value.

        Called by Qt to refresh an already-open editor when the underlying
        model data changes.

        Args:
            editor: The ``QComboBox`` editor widget.
            index: Model index of the cell being edited.
        """
        current = index.data(Qt.ItemDataRole.EditRole)
        if current:
            idx = editor.findText(str(current))
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                editor.setEditText(str(current))

    def setModelData(self, editor, model, index):
        """Write the combo box's current text back to the model.

        Called by Qt when the editor is committed (e.g. user presses Enter or
        clicks away).

        Args:
            editor: The ``QComboBox`` editor widget.
            model: The item model to update.
            index: Model index of the cell being edited.
        """
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

class _RxSortProxy(QSortFilterProxyModel):
    """Sort proxy for the RX message tree view.

    Keeps decoded signal child rows in their DBC field order while allowing
    top-level message rows to be sorted by clicking any column header.
    Two columns receive special numeric treatment:

    - **Column 1** (ID hex): compared as integers so that ``"00A" < "100"``
      rather than lexicographically.
    - **Column 8** (Cycle Time): compared as floats so that ``"12.3"`` sorts
      after ``"9.5"`` rather than before it.
    """

    def lessThan(self, left, right) -> bool:
        """Compare two source-model indexes for sort ordering.

        Signal child rows (any index with a valid parent) always return
        ``False`` to preserve their natural DBC definition order regardless of
        the active column sort.

        Args:
            left: Left-hand source model index.
            right: Right-hand source model index.

        Returns:
            ``True`` if *left* should appear before *right* in ascending order.
        """
        # Signal child rows — keep natural DBC field order
        if left.parent().isValid():
            return False

        col = left.column()

        if col == 1:  # ID (hex) — compare as integer
            try:
                return int(left.data() or "0", 16) < int(right.data() or "0", 16)
            except (ValueError, TypeError):
                pass

        if col == 8:  # Cycle Time (displayed as "12.3" or "") — compare as float
            try:
                lv = float(left.data() or 0)
            except (ValueError, TypeError):
                lv = 0.0
            try:
                rv = float(right.data() or 0)
            except (ValueError, TypeError):
                rv = 0.0
            return lv < rv

        return super().lessThan(left, right)


# RX/TX column widths: Bus, ID(hex), Ext, Type, Length, Symbol, Data(hex), ...
_DEFAULT_WIDTHS = [40, 80, 35, 50, 50, 120, 200, 80, 60, 60, 60]

# Connection table: (empty/checkbox), Bus, Name, Channel, Interface, Bit Rate,
#                   Status, Overruns, QXmtFulls, Options  (Bus Load stretches)
_DEFAULT_CONN_WIDTHS = [28, 38, 80, 110, 120, 80, 60, 68, 68, 48]


class RxTxWindow(BaseDockWindow):
    """Main Receive/Transmit panel.

    Displays three vertically-stacked panes:

    * **RX pane** — a ``QTreeView`` backed by
      :class:`~cangui.model_rx_message.RxMessageModel` showing incoming CAN
      frames with optional decoded signals as child rows.
    * **TX pane** — a ``QTreeView`` backed by
      :class:`~cangui.model_tx_message.TxMessageModel` for configuring and
      sending CAN frames, each with its own toolbar.
    * **Connections pane** — a ``QTreeView`` backed by
      :class:`~cangui.model_connection.ConnectionModel` for managing CAN bus
      connections.

    Selection in any pane is made exclusive: choosing a row in one pane
    automatically clears selections in the other two.  A
    :class:`_ClickOutsideFilter` additionally clears all selections when the
    user clicks on empty space inside the window.
    """

    TITLE = "Receive / Transmit"

    add_tx_requested = Signal()
    """Emitted when the Add Frame toolbar button is clicked in the TX pane."""
    add_to_watch_requested = Signal(int, str, str, str)  # arb_id, signal_name, unit, direction
    """Emitted to add a signal to the Watch window.

    Args (signal parameters):
        arb_id (int): CAN arbitration ID of the parent message.
        signal_name (str): Name of the decoded signal.
        unit (str): Physical unit string for the signal.
        direction (str): ``"Rx"`` or ``"Tx"``.
    """
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit
    """Emitted to add a signal to the Plot window.

    Args (signal parameters):
        arb_id (int): CAN arbitration ID of the parent message.
        signal_name (str): Name of the decoded signal.
        unit (str): Physical unit string for the signal.
    """
    add_connection_requested = Signal()
    """Emitted when the Add toolbar button is clicked in the Connections pane."""
    reset_connections_requested = Signal()
    """Emitted when the Reset toolbar button is clicked in the Connections pane."""

    def __init__(self, rx_model: RxMessageModel, tx_model: TxMessageModel,
                 connection_model: ConnectionModel, parent=None):
        """Initialise the RX/TX window and build all panes.

        Creates the RX toolbar and tree view, the TX toolbar and tree view
        (with :class:`SymbolDelegate` on the Symbol column), and the
        Connections toolbar and tree view.  Wires exclusive-selection logic
        and installs the click-outside event filter.

        Args:
            rx_model: Data model supplying received CAN messages.
            tx_model: Data model for configuring outgoing CAN messages.
            connection_model: Data model for CAN bus connections.
            parent: Optional parent widget.
        """
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

        self._rx_proxy = _RxSortProxy(self)
        self._rx_proxy.setSourceModel(self._rx_model)

        self._rx_view = TabTreeView()
        self._rx_view.setRootIsDecorated(True)
        self._rx_view.setAlternatingRowColors(True)
        self._rx_view.setModel(self._rx_proxy)
        self._rx_view.setSortingEnabled(True)
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
        """The vertical QSplitter that separates the RX, TX, and Connections panes.

        Returns:
            The internal :class:`QSplitter` instance; callers may read its
            sizes or connect to its ``splitterMoved`` signal.
        """
        return self._splitter

    @property
    def primary_view(self):
        """The primary focusable view for the focus manager (the RX tree view).

        Returns:
            The :class:`~cangui.ui_tab_navigation.TabTreeView` showing
            received messages.
        """
        return self._rx_view

    @property
    def selectable_views(self):
        """All three tree views that participate in the exclusive-selection scheme.

        Returns:
            A list containing the RX, TX, and Connections tree views.
        """
        return [self._rx_view, self._tx_view, self._conn_view]

    @staticmethod
    def _set_default_widths(view: QTreeView, widths: list[int] = _DEFAULT_WIDTHS):
        """Apply initial column widths to a tree view header.

        Iterates over *widths* and resizes each corresponding header section.
        Sections beyond the number of columns in the header are silently
        ignored.

        Args:
            view: The tree view whose header columns will be resized.
            widths: Ordered list of pixel widths; defaults to
                ``_DEFAULT_WIDTHS``.
        """
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
        """Register the callable used to transmit a single CAN message.

        The callback is invoked by :meth:`_send_once` with a constructed
        :class:`~cangui.can_message.CanMessage` instance.

        Args:
            callback: A callable with the signature ``callback(msg)`` where
                *msg* is a :class:`~cangui.can_message.CanMessage`.
        """
        self._send_once_callback = callback

    # -- Exclusive selection across the three sub-views --

    def _on_rx_selected(self, selected, _):
        """Clear TX and Connections selections when a row is chosen in the RX view.

        Triggered by the RX view's ``selectionModel().selectionChanged`` signal.

        Args:
            selected: ``QItemSelection`` containing the newly selected indexes.
            _: Deselected items (unused).
        """
        if selected.indexes():
            self._tx_view.clearSelection()
            self._conn_view.clearSelection()

    def _on_tx_selected(self, selected, _):
        """Clear RX and Connections selections when a row is chosen in the TX view.

        Triggered by the TX view's ``selectionModel().selectionChanged`` signal.

        Args:
            selected: ``QItemSelection`` containing the newly selected indexes.
            _: Deselected items (unused).
        """
        if selected.indexes():
            self._rx_view.clearSelection()
            self._conn_view.clearSelection()

    def _on_conn_selected(self, selected, _):
        """Clear RX and TX selections when a row is chosen in the Connections view.

        Triggered by the Connections view's
        ``selectionModel().selectionChanged`` signal.

        Args:
            selected: ``QItemSelection`` containing the newly selected indexes.
            _: Deselected items (unused).
        """
        if selected.indexes():
            self._rx_view.clearSelection()
            self._tx_view.clearSelection()

    def _on_clear(self):
        """Clear all received messages from the RX model."""
        self._rx_model.clear()

    def _remove_tx(self):
        """Remove the selected TX message (resolving child-index to parent row).

        Works whether a top-level message row or a child signal row is selected:
        both map to the parent message row before calling ``remove_message``.
        """
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        if index.parent().isValid():
            row = index.parent().row()
        else:
            row = index.row()
        self._tx_model.remove_message(row)

    def _clear_tx_counters(self):
        """Reset the send-count column of all TX messages to zero."""
        self._tx_model.clear_counts()

    def _move_tx_up(self):
        """Move the selected TX message one row up and follow the selection.

        Resolves a child signal index to its parent message row before moving.
        """
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        row = index.parent().row() if index.parent().isValid() else index.row()
        self._tx_model.move_up(row)
        self._tx_view.setCurrentIndex(self._tx_model.index(row - 1, 0))

    def _move_tx_down(self):
        """Move the selected TX message one row down and follow the selection.

        Resolves a child signal index to its parent message row before moving.
        """
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        row = index.parent().row() if index.parent().isValid() else index.row()
        self._tx_model.move_down(row)
        self._tx_view.setCurrentIndex(self._tx_model.index(row + 1, 0))

    def _duplicate_tx(self):
        """Deep-copy the selected TX message and append it with count and cycle reset.

        The clone has its ``count`` set to 0 and ``cycle_enabled`` set to
        ``False`` so the copy starts in a clean, stopped state.
        """
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
        """Remove the currently selected connection row from the connection model."""
        index = self._conn_view.currentIndex()
        if index.isValid():
            self._connection_model.remove_row(index.row())

    def _send_once(self):
        """Transmit the selected TX message once and increment its send counter.

        Builds a :class:`~cangui.can_message.CanMessage` from the selected
        TxItem and invokes the registered ``_send_once_callback``.  Does
        nothing if no callback has been registered or no row is selected.
        """
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
        """Show a context menu on the RX view with Watch and Plot options.

        If the right-clicked index resolves to a decoded signal, the menu
        offers single-signal Watch/Plot actions.  If it resolves to a message
        row that has signals, bulk "Add All Signals" actions are offered instead.

        Args:
            pos: Viewport-relative position of the right-click event.
        """
        index = self._rx_view.indexAt(pos)
        if not index.isValid():
            return

        # Map proxy index → source index before querying the source model
        src_index = self._rx_proxy.mapToSource(index)

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)

        result = self._rx_model.get_signal_at(src_index)
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
            item = self._rx_model.get_item(src_index)
            if item and item.signals:
                action = menu.addAction("Add All Signals to Watch")
                action.triggered.connect(lambda: self._add_all_rx_to_watch(item))
                plot_action = menu.addAction("Add All Signals to Plot")
                plot_action.triggered.connect(lambda: self._add_all_rx_to_plot(item))

        if not menu.isEmpty():
            menu.exec(self._rx_view.viewport().mapToGlobal(pos))

    def _add_rx_to_watch(self):
        """Add the selected RX signal (or all signals of the selected message) to Watch.

        Emits:
            add_to_watch_requested: For each signal added, with
                ``(can_id, name, unit, "Rx")``.
        """
        index = self._rx_view.currentIndex()
        if not index.isValid():
            return
        src_index = self._rx_proxy.mapToSource(index)
        result = self._rx_model.get_signal_at(src_index)
        if result is not None:
            item, sig = result
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")
        else:
            item = self._rx_model.get_item(src_index)
            if item and item.signals:
                self._add_all_rx_to_watch(item)

    def _add_rx_to_plot(self):
        """Add the selected RX signal (or all signals of the selected message) to Plot.

        Emits:
            add_to_plot_requested: For each signal added, with
                ``(can_id, name, unit)``.
        """
        index = self._rx_view.currentIndex()
        if not index.isValid():
            return
        src_index = self._rx_proxy.mapToSource(index)
        result = self._rx_model.get_signal_at(src_index)
        if result is not None:
            item, sig = result
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
        else:
            item = self._rx_model.get_item(src_index)
            if item and item.signals:
                self._add_all_rx_to_plot(item)

    def _add_tx_to_watch(self):
        """Add the selected TX signal (or all signals of the selected message) to Watch.

        Emits:
            add_to_watch_requested: For each signal added, with
                ``(can_id, name, unit, "Tx")``.
        """
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
        """Add the selected TX signal (or all signals of the selected message) to Plot.

        Emits:
            add_to_plot_requested: For each signal added, with
                ``(can_id, name, unit)``.
        """
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
        """Emit ``add_to_watch_requested`` for every decoded signal in an RX item.

        Args:
            item: RxItem whose decoded signal list should be broadcast.

        Emits:
            add_to_watch_requested: Once per signal with
                ``(can_id, name, unit, "Rx")``.
        """
        for sig in item.signals:
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")

    def _add_all_rx_to_plot(self, item):
        """Emit ``add_to_plot_requested`` for every decoded signal in an RX item.

        Args:
            item: RxItem whose decoded signal list should be broadcast.

        Emits:
            add_to_plot_requested: Once per signal with ``(can_id, name, unit)``.
        """
        for sig in item.signals:
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
