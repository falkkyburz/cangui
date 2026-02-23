"""Receive/Transmit window module for cangui.

Provides :class:`RxTxWindow`, the main RX/TX panel arranged in a vertical
three-pane splitter (received messages, transmit messages, CAN connections).
Helper classes :class:`_ClickOutsideFilter` and :class:`SymbolDelegate` are
defined here as well.
"""
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHeaderView, QTreeView, QSplitter,
    QStyledItemDelegate, QToolBar, QLabel, QWidget, QVBoxLayout, QMenu, QAbstractItemView,
)
from PySide6.QtGui import QAction, QFont
from PySide6.QtCore import Qt, Signal, QEvent, QObject, QSortFilterProxyModel, QTimer

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
                    from PySide6.QtWidgets import QAbstractButton
                    if not isinstance(target, QAbstractButton):
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


class _HexFontDelegate(QStyledItemDelegate):
    """Renders a column using a monospaced font for hex data display."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # Only top-level message rows in the Data (hex) column should be monospace.
        if not index.parent().isValid():
            option.font.setFamily("Courier New")
            option.font.setStyleHint(QFont.StyleHint.Monospace)


class TxSignalValueDelegate(QStyledItemDelegate):
    """Delegate for the TX signal value column.

    Creates an editable ``QComboBox`` when the signal has a value table
    (``choices`` stored under ``Qt.ItemDataRole.UserRole``), otherwise falls
    back to the default line-edit.  The Data (hex) column for top-level rows
    is rendered with a monospaced font.
    """

    def initStyleOption(self, option, index):
        """Apply a monospaced font for top-level hex data display."""
        super().initStyleOption(option, index)
        if not index.parent().isValid():
            option.font.setFamily("Courier New")
            option.font.setStyleHint(QFont.StyleHint.Monospace)

    def createEditor(self, parent, option, index):
        """Return an editable combobox for enum signals, or the default editor.

        Args:
            parent: Parent widget for the editor.
            option: Style option (unused).
            index: Model index being edited.

        Returns:
            Editable ``QComboBox`` pre-loaded with named values when choices
            are available; the standard ``QLineEdit`` otherwise.
        """
        choices = index.data(Qt.ItemDataRole.UserRole)
        if choices:
            combo = QComboBox(parent)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItems(choices)
            QTimer.singleShot(0, combo.showPopup)
            return combo
        editor = super().createEditor(parent, option, index)

        # Top-level Data (hex) editor: defer live TX overlay updates while open.
        if editor is not None and not index.parent().isValid():
            model = index.model()
            source_model = model
            source_index = index
            while isinstance(source_model, QSortFilterProxyModel):
                source_index = source_model.mapToSource(source_index)
                source_model = source_model.sourceModel()
            if hasattr(source_model, "set_raw_hex_editing"):
                source_row = source_index.row()
                source_model.set_raw_hex_editing(source_row, True)
                editor.destroyed.connect(
                    lambda *_,
                    m=source_model,
                    r=source_row: m.set_raw_hex_editing(r, False)
                )

        return editor

    def setEditorData(self, editor, index):
        """Synchronise the combobox with the current model value.

        Args:
            editor: The editor widget returned by ``createEditor``.
            index: The model index being edited.
        """
        if isinstance(editor, QComboBox):
            current = index.data(Qt.ItemDataRole.DisplayRole) or ""
            current_str = str(current)
            idx = editor.findText(current_str)
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                editor.setEditText(current_str)
            line_edit = editor.lineEdit()
            if line_edit is not None:
                # Pre-select the whole value so typing immediately replaces it.
                QTimer.singleShot(0, line_edit.selectAll)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        """Write the selected or typed value back to the model.

        When the text has the ``"N = Name"`` format produced by
        :meth:`~cangui.signal_decoder.SignalDecoder.get_signals_for_id`, only
        the ``Name`` part is written so the underlying encoder receives the
        plain string name.

        Args:
            editor: The editor widget.
            model: The item model to update.
            index: The model index to update.
        """
        if isinstance(editor, QComboBox):
            text = editor.currentText()
            parts = text.split(" = ", 1)
            value = parts[1] if len(parts) == 2 else text
            model.setData(index, value, Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


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
        # Unwrap any proxy layers to reach the source model
        source_model = model
        while hasattr(source_model, "sourceModel"):
            source_model = source_model.sourceModel()
        if hasattr(source_model, "get_all_symbols"):
            symbols = source_model.get_all_symbols()
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
    - **Column 7** (Cycle Time): compared as floats so that ``"12.3"`` sorts
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

        if col == 7:  # Cycle Time (displayed as "12.3" or "") — compare as float
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


class _TxSortProxy(QSortFilterProxyModel):
    """Sort proxy for the TX message tree view.

    Keeps signal child rows in their natural DBC field order while allowing
    top-level message rows to be sorted by clicking any column header.  The
    ID (hex) column receives special numeric treatment.
    """

    def lessThan(self, left, right) -> bool:
        """Compare two source-model indexes for sort ordering.

        Signal child rows always return ``False`` to preserve their natural
        DBC definition order.

        Args:
            left: Left-hand source model index.
            right: Right-hand source model index.

        Returns:
            ``True`` if *left* should appear before *right* in ascending order.
        """
        if left.parent().isValid():
            return False

        col = left.column()

        if col == 1:  # ID (hex) — compare as integer
            try:
                return int(left.data() or "0", 16) < int(right.data() or "0", 16)
            except (ValueError, TypeError):
                pass

        return super().lessThan(left, right)


# RX column widths: Bus, ID(hex), Ext, Type, Length, Symbol, Data(hex),
#                   Cycle Time, Count, Timing Errors
_DEFAULT_RX_WIDTHS = [44, 80, 35, 50, 44, 120, 160, 58, 60, 88]

# TX column widths: Bus, ID(hex), Ext, Type, Length, Symbol, Data(hex),
#                   Cycle Time, Count, Trigger, Creator
_DEFAULT_TX_WIDTHS = [44, 80, 35, 50, 44, 120, 160, 58, 60, 60, 60]

# Connection table: (empty/checkbox), Bus, Name, Channel, Interface, Bit Rate,
#                   Status, Listen Only, Bus Load
_DEFAULT_CONN_WIDTHS = [28, 38, 90, 110, 120, 84, 72, 84, 84]


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
        self._rx_width_ratios = self._width_ratios(_DEFAULT_RX_WIDTHS)
        self._tx_width_ratios = self._width_ratios(_DEFAULT_TX_WIDTHS)
        self._conn_width_ratios = self._width_ratios(_DEFAULT_CONN_WIDTHS)

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
        self._rx_view.header().setStretchLastSection(False)
        self._rx_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._rx_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._rx_view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self._rx_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._rx_view.customContextMenuRequested.connect(self._rx_context_menu)
        self._rx_view.setItemDelegateForColumn(6, _HexFontDelegate(self._rx_view))
        self._set_default_widths(self._rx_view, _DEFAULT_RX_WIDTHS)
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

        self._add_tx_action = QAction(_icon("add"), "Add Frame", self)
        self._add_tx_action.triggered.connect(self.add_tx_requested)
        tx_toolbar.addAction(self._add_tx_action)

        self._remove_tx_action = QAction(_icon("remove"), "Remove", self)
        self._remove_tx_action.triggered.connect(self._remove_tx)
        tx_toolbar.addAction(self._remove_tx_action)

        self._duplicate_tx_action = QAction(_icon("copy"), "Duplicate", self)
        self._duplicate_tx_action.triggered.connect(self._duplicate_tx)
        tx_toolbar.addAction(self._duplicate_tx_action)

        self._send_once_action = QAction(_icon("send"), "Send Once", self)
        self._send_once_action.triggered.connect(self._send_once)
        tx_toolbar.addAction(self._send_once_action)

        clear_counters_action = QAction(_icon("clear"), "Clear Counters", self)
        clear_counters_action.triggered.connect(self._clear_tx_counters)
        tx_toolbar.addAction(clear_counters_action)

        tx_toolbar.addSeparator()

        self._up_tx_action = QAction(_icon("up"), "Move Up", self)
        self._up_tx_action.triggered.connect(self._move_tx_up)
        tx_toolbar.addAction(self._up_tx_action)

        self._down_tx_action = QAction(_icon("down"), "Move Down", self)
        self._down_tx_action.triggered.connect(self._move_tx_down)
        tx_toolbar.addAction(self._down_tx_action)

        tx_toolbar.addSeparator()

        self._add_tx_watch_action = QAction(_icon("watch"), "Add to Watch", self)
        self._add_tx_watch_action.triggered.connect(self._add_tx_to_watch)
        tx_toolbar.addAction(self._add_tx_watch_action)

        self._add_tx_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        self._add_tx_plot_action.triggered.connect(self._add_tx_to_plot)
        tx_toolbar.addAction(self._add_tx_plot_action)

        tx_layout.addWidget(tx_toolbar)

        self._tx_proxy = _TxSortProxy(self)
        self._tx_proxy.setSourceModel(self._tx_model)

        self._tx_view = TabTreeView()
        self._tx_view.setRootIsDecorated(True)
        self._tx_view.setAlternatingRowColors(True)
        self._tx_view.setModel(self._tx_proxy)
        self._tx_view.setSortingEnabled(True)
        self._tx_view.header().setStretchLastSection(False)
        self._tx_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._tx_view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._tx_view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self._tx_view.setItemDelegateForColumn(5, SymbolDelegate(self._tx_view))
        self._tx_view.setItemDelegateForColumn(6, TxSignalValueDelegate(self._tx_view))
        self._tx_view.setDragEnabled(True)
        self._tx_view.setAcceptDrops(True)
        self._tx_view.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._tx_view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tx_view.setDragDropOverwriteMode(False)
        self._tx_view.setDropIndicatorShown(True)
        self._tx_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tx_view.customContextMenuRequested.connect(self._tx_context_menu)
        self._set_default_widths(self._tx_view, _DEFAULT_TX_WIDTHS)
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

        self._add_conn_action = QAction(_icon("add"), "Add", self)
        self._add_conn_action.triggered.connect(self.add_connection_requested)
        conn_toolbar.addAction(self._add_conn_action)

        self._remove_conn_action = QAction(_icon("remove"), "Remove", self)
        self._remove_conn_action.triggered.connect(self._remove_connection)
        conn_toolbar.addAction(self._remove_conn_action)

        self._reset_conn_action = QAction(_icon("refresh"), "Reset", self)
        self._reset_conn_action.triggered.connect(self.reset_connections_requested)
        conn_toolbar.addAction(self._reset_conn_action)

        conn_layout.addWidget(conn_toolbar)

        self._conn_view = TabTreeView()
        self._conn_view.setRootIsDecorated(False)
        self._conn_view.setAlternatingRowColors(True)
        self._conn_view.setModel(self._connection_model)
        self._conn_view.setItemDelegateForColumn(4, InterfaceDelegate(self._conn_view))
        self._conn_view.header().setStretchLastSection(False)
        self._conn_view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._conn_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._conn_view.customContextMenuRequested.connect(self._conn_context_menu)
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
        QTimer.singleShot(0, self._apply_proportional_widths_all)

    def resizeEvent(self, event):
        """Reapply proportional column widths when this panel is resized."""
        super().resizeEvent(event)
        self._apply_proportional_widths_all()

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
    def _set_default_widths(view: QTreeView, widths: list[int]):
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

    @staticmethod
    def _width_ratios(widths: list[int]) -> list[float]:
        """Convert absolute widths into normalized per-column ratios."""
        total = sum(widths) or 1
        return [w / total for w in widths]

    def _apply_proportional_widths_all(self):
        """Apply proportional widths for RX, TX, and Connections views."""
        self._apply_proportional_widths(self._rx_view, self._rx_width_ratios)
        self._apply_proportional_widths(self._tx_view, self._tx_width_ratios)
        self._apply_proportional_widths(self._conn_view, self._conn_width_ratios)

    @staticmethod
    def _apply_proportional_widths(view: QTreeView, ratios: list[float]):
        """Resize columns by ratios so they track current viewport width."""
        header = view.header()
        count = min(header.count(), len(ratios))
        available = view.viewport().width()
        if count <= 0 or available <= 0:
            return
        widths = [int(available * ratios[i]) for i in range(count)]
        widths[-1] += available - sum(widths)
        for i, width in enumerate(widths):
            header.resizeSection(i, max(16, width))

    def send_space_pressed(self):
        """Send selected TX messages once (invoked by the Space key via FocusManager)."""
        self._send_once()

    def edit_last_tx_can_id(self):
        """Start editing the CAN-ID cell of the last TX row."""
        last_row = self._tx_model.rowCount() - 1
        if last_row < 0:
            return
        src_idx = self._tx_model.index(last_row, 1)  # column 1 = CAN-ID
        proxy_idx = self._tx_proxy.mapFromSource(src_idx)
        self._tx_view.setCurrentIndex(proxy_idx)
        self._tx_view.scrollTo(proxy_idx)
        self._tx_view.edit(proxy_idx)

    def set_send_once_callback(self, callback):
        """Register the callable used to transmit a single CAN message.

        The callback is invoked by :meth:`_send_once` with a constructed
        :class:`~cangui.can_message.CanMessage` instance.

        Args:
            callback: A callable with the signature ``callback(msg) -> bool``
                where *msg* is a :class:`~cangui.can_message.CanMessage` and
                the return value indicates transport success.
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

    # -- Selection helpers -------------------------------------------------

    def _rx_proxy_selection(self) -> list:
        """Return the RX selection, falling back to the current index.

        Uses ``selectedIndexes()`` filtered to column 0 rather than
        ``selectedRows()``, which is more robust when a sort/filter proxy
        is installed and not all columns are selected in the selection model.

        Returns:
            List of proxy ``QModelIndex`` objects at column 0.
        """
        col0 = [i for i in self._rx_view.selectionModel().selectedIndexes()
                if i.column() == 0]
        if not col0:
            cur = self._rx_view.currentIndex()
            if cur.isValid():
                col0 = [self._rx_view.model().index(cur.row(), 0, cur.parent())]
        return col0

    def _tx_proxy_selection(self) -> list:
        """Return the TX selection, falling back to the current index.

        Uses ``selectedIndexes()`` filtered to column 0 rather than
        ``selectedRows()``, which is more robust when a sort/filter proxy
        is installed and not all columns are selected in the selection model.

        Returns:
            List of proxy ``QModelIndex`` objects at column 0.
        """
        col0 = [i for i in self._tx_view.selectionModel().selectedIndexes()
                if i.column() == 0]
        if not col0:
            cur = self._tx_view.currentIndex()
            if cur.isValid():
                col0 = [self._tx_view.model().index(cur.row(), 0, cur.parent())]
        return col0

    def _rx_selected_signals(self) -> list:
        """Collect all unique (RxMessageItem, SignalItem) pairs from the RX selection.

        Selection rules:

        * **Message row selected** → every decoded signal of that message is
          included (recursive expansion into children).
        * **Signal child row selected, parent NOT selected** → only that specific
          signal is included.
        * **Both parent and child selected** → the child is *skipped* because
          the parent expansion already covers it; no signal appears twice.

        Returns:
            Ordered list of ``(RxMessageItem, SignalItem)`` pairs, deduplicated
            by ``(can_id, signal_name)``.
        """
        proxy_sel = self._rx_proxy_selection()

        # First pass: record which top-level message rows are directly selected.
        selected_msg_rows: set[int] = set()
        for pidx in proxy_sel:
            src = self._rx_proxy.mapToSource(pidx)
            if not src.parent().isValid():
                selected_msg_rows.add(src.row())

        result: list = []
        seen: set = set()   # (can_id, signal_name) deduplication

        for pidx in proxy_sel:
            src = self._rx_proxy.mapToSource(pidx)
            if not src.parent().isValid():
                # Top-level message row → expand to all decoded signals.
                item = self._rx_model.get_item(src)
                if item:
                    for sig in item.signals:
                        key = (item.can_id, sig.name)
                        if key not in seen:
                            seen.add(key)
                            result.append((item, sig))
            else:
                # Signal child row → include only when its parent is NOT selected
                # (otherwise the parent expansion already covers this signal).
                if src.parent().row() in selected_msg_rows:
                    continue
                pair = self._rx_model.get_signal_at(src)
                if pair:
                    item, sig = pair
                    key = (item.can_id, sig.name)
                    if key not in seen:
                        seen.add(key)
                        result.append((item, sig))

        return result

    def _tx_selected_signals(self) -> list:
        """Collect all unique (TxMessageItem, TxSignalItem) pairs from the TX selection.

        Applies the same parent-priority / deduplication logic as
        :meth:`_rx_selected_signals`.

        Returns:
            Ordered list of ``(TxMessageItem, TxSignalItem)`` pairs.
        """
        proxy_sel = self._tx_proxy_selection()

        selected_msg_rows: set[int] = set()
        for pidx in proxy_sel:
            src = self._tx_proxy.mapToSource(pidx)
            if not src.parent().isValid():
                selected_msg_rows.add(src.row())

        result: list = []
        seen: set = set()

        for pidx in proxy_sel:
            src = self._tx_proxy.mapToSource(pidx)
            if not src.parent().isValid():
                item = self._tx_model.get_item(src.row())
                if item:
                    for sig in item.signals:
                        key = (item.can_id, sig.name)
                        if key not in seen:
                            seen.add(key)
                            result.append((item, sig))
            else:
                if src.parent().row() in selected_msg_rows:
                    continue
                pair = self._tx_model.get_signal_at(src)
                if pair:
                    item, sig = pair
                    key = (item.can_id, sig.name)
                    if key not in seen:
                        seen.add(key)
                        result.append((item, sig))

        return result

    def _tx_selected_message_rows(self) -> list[int]:
        """Return sorted, unique source-model message row indices for the TX selection.

        Both top-level message rows and signal child rows resolve to their
        parent message row.

        Returns:
            Sorted list of unique source-model row indices.
        """
        rows: set[int] = set()
        for pidx in self._tx_proxy_selection():
            src = self._tx_proxy.mapToSource(pidx)
            rows.add(src.parent().row() if src.parent().isValid() else src.row())
        return sorted(rows)

    # -- Action handlers ---------------------------------------------------

    def _remove_tx(self):
        """Remove all selected TX messages.

        Resolves signal child rows to their parent message.  Rows are removed
        in reverse order so earlier indices remain valid throughout.
        """
        for row in sorted(self._tx_selected_message_rows(), reverse=True):
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
        src = self._tx_proxy.mapToSource(index)
        row = src.parent().row() if src.parent().isValid() else src.row()
        self._tx_model.move_up(row)
        new_proxy = self._tx_proxy.mapFromSource(self._tx_model.index(row - 1, 0))
        self._tx_view.setCurrentIndex(new_proxy)

    def _move_tx_down(self):
        """Move the selected TX message one row down and follow the selection.

        Resolves a child signal index to its parent message row before moving.
        """
        index = self._tx_view.currentIndex()
        if not index.isValid():
            return
        src = self._tx_proxy.mapToSource(index)
        row = src.parent().row() if src.parent().isValid() else src.row()
        self._tx_model.move_down(row)
        new_proxy = self._tx_proxy.mapFromSource(self._tx_model.index(row + 1, 0))
        self._tx_view.setCurrentIndex(new_proxy)

    def _duplicate_tx(self):
        """Deep-copy all selected TX messages and append them with count / cycle reset."""
        from copy import deepcopy
        for row in self._tx_selected_message_rows():
            item = self._tx_model.get_item(row)
            if item:
                clone = deepcopy(item)
                clone.count = 0
                clone.cycle_enabled = False
                self._tx_model.add_message(clone)

    def _remove_connection(self):
        """Remove all selected connection rows from the connection model."""
        selected = self._conn_view.selectionModel().selectedRows()
        if not selected:
            index = self._conn_view.currentIndex()
            if index.isValid():
                selected = [index]
        rows = sorted({idx.row() for idx in selected}, reverse=True)
        for row in rows:
            self._connection_model.remove_row(row)

    def _send_once(self):
        """Transmit all selected TX messages once and increment their send counters.

        Signal child rows resolve to their parent message.  All uniquely
        selected messages are sent in ascending row order.
        """
        if not hasattr(self, "_send_once_callback"):
            return
        from cangui.can_message import CanMessage
        for row in self._tx_selected_message_rows():
            item = self._tx_model.get_item(row)
            if item:
                msg = CanMessage(
                    arbitration_id=item.can_id,
                    data=bytes(item.raw_data),
                    is_extended_id=item.is_extended_id,
                    dlc=item.length,
                    bus=item.bus,
                    row=row,
                )
                if self._send_once_callback(msg):
                    self._tx_model.increment_count(row)

    def _tx_context_menu(self, pos):
        """Show a context menu for the TX view."""
        has_sel = self._tx_view.selectionModel().hasSelection()
        self._remove_tx_action.setEnabled(has_sel)
        self._duplicate_tx_action.setEnabled(has_sel)
        self._send_once_action.setEnabled(has_sel)
        self._up_tx_action.setEnabled(has_sel)
        self._down_tx_action.setEnabled(has_sel)
        self._add_tx_watch_action.setEnabled(has_sel)
        self._add_tx_plot_action.setEnabled(has_sel)
        menu = QMenu(self)
        menu.addAction(self._add_tx_action)
        menu.addAction(self._remove_tx_action)
        menu.addAction(self._duplicate_tx_action)
        menu.addSeparator()
        menu.addAction(self._send_once_action)
        menu.addSeparator()
        menu.addAction(self._up_tx_action)
        menu.addAction(self._down_tx_action)
        menu.addSeparator()
        menu.addAction(self._add_tx_watch_action)
        menu.addAction(self._add_tx_plot_action)
        menu.exec(self._tx_view.viewport().mapToGlobal(pos))

    def _conn_context_menu(self, pos):
        """Show a context menu for the Connections view."""
        has_sel = self._conn_view.selectionModel().hasSelection()
        self._remove_conn_action.setEnabled(has_sel)
        menu = QMenu(self)
        menu.addAction(self._add_conn_action)
        menu.addAction(self._remove_conn_action)
        menu.addSeparator()
        menu.addAction(self._reset_conn_action)
        menu.exec(self._conn_view.viewport().mapToGlobal(pos))

    def _rx_context_menu(self, pos):
        """Show a context menu for the RX view that respects the full multi-selection.

        The menu labels report how many signals will be affected so the user
        knows the scope of the action before clicking.

        Args:
            pos: Viewport-relative position of the right-click event.
        """
        if not self._rx_view.indexAt(pos).isValid():
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)

        pairs = self._rx_selected_signals()
        n = len(pairs)
        if n == 0:
            return
        elif n == 1:
            item, sig = pairs[0]
            watch_lbl = f"Add '{sig.name}' to Watch"
            plot_lbl  = f"Add '{sig.name}' to Plot"
        else:
            watch_lbl = f"Add {n} signals to Watch"
            plot_lbl  = f"Add {n} signals to Plot"

        menu.addAction(watch_lbl).triggered.connect(self._add_rx_to_watch)
        menu.addAction(plot_lbl).triggered.connect(self._add_rx_to_plot)
        menu.exec(self._rx_view.viewport().mapToGlobal(pos))

    def _add_rx_to_watch(self):
        """Add all uniquely-selected RX signals to Watch.

        Selecting a message row recursively includes all its decoded signals.
        Selecting individual signal children includes only those signals.
        No signal is emitted twice even when both parent and children are
        selected simultaneously.

        Emits:
            add_to_watch_requested: Once per unique signal with
                ``(can_id, name, unit, "Rx")``.
        """
        for item, sig in self._rx_selected_signals():
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Rx")

    def _add_rx_to_plot(self):
        """Add all uniquely-selected RX signals to Plot.

        Applies the same recursive / deduplication logic as
        :meth:`_add_rx_to_watch`.

        Emits:
            add_to_plot_requested: Once per unique signal with
                ``(can_id, name, unit)``.
        """
        for item, sig in self._rx_selected_signals():
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)

    def _add_tx_to_watch(self):
        """Add all uniquely-selected TX signals to Watch.

        Selecting a message row recursively includes all its decoded signals.
        No signal is emitted twice even when both parent and children are
        selected simultaneously.

        Emits:
            add_to_watch_requested: Once per unique signal with
                ``(can_id, name, unit, "Tx")``.
        """
        for item, sig in self._tx_selected_signals():
            self.add_to_watch_requested.emit(item.can_id, sig.name, sig.unit, "Tx")

    def _add_tx_to_plot(self):
        """Add all uniquely-selected TX signals to Plot.

        Applies the same recursive / deduplication logic as
        :meth:`_add_tx_to_watch`.

        Emits:
            add_to_plot_requested: Once per unique signal with
                ``(can_id, name, unit)``.
        """
        for item, sig in self._tx_selected_signals():
            self.add_to_plot_requested.emit(item.can_id, sig.name, sig.unit)
