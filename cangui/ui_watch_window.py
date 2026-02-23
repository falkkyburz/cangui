"""Watch dock window for cangui.

Displays the most-recently-decoded value for each pinned CAN signal, backed
by :class:`~cangui.model_watch.WatchModel`.  Signals can be arranged across
two panes (left and right) using the transfer arrow buttons in the toolbar.
"""
from PySide6.QtWidgets import (
    QHeaderView, QTreeView, QToolBar, QMenu, QAbstractItemView,
    QWidget, QHBoxLayout,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal, QTimer, Qt, QSortFilterProxyModel, QModelIndex

from cangui.model_watch import WatchModel
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.icons import icon as _icon


class WatchPaneProxy(QSortFilterProxyModel):
    """Filter proxy that shows only entries belonging to a specific pane.

    Args:
        pane: The pane index to keep visible (0 = left, 1 = right).
    """

    def __init__(self, pane: int, parent=None):
        super().__init__(parent)
        self._pane = pane

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None or source_row >= len(model.entries):
            return False
        return model.entries[source_row].pane == self._pane


class WatchWindow(BaseDockWindow):
    """Watch panel that shows the latest decoded value for each pinned signal.

    Backed by :class:`~cangui.model_watch.WatchModel`.  The view is split into
    two panes (left and right); signals can be moved between panes using the
    arrow buttons in the toolbar.

    Signals:
        remove_requested: Emitted with the row index when a signal is removed.
        add_to_plot_requested: Emitted with ``(arb_id, signal_name, unit)``
            when the user clicks "Add to Plot".
    """

    TITLE = "Watch"

    remove_requested = Signal(int)  # row
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, model: WatchModel, parent=None):
        """Build the Watch panel with its toolbar and dual-pane table views.

        Args:
            model: The :class:`~cangui.model_watch.WatchModel` driving both views.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._remove_action = QAction(_icon("remove"), "Remove", self)
        self._remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(self._remove_action)

        clear_action = QAction(_icon("trash"), "Clear All", self)
        clear_action.triggered.connect(self._model.clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        self._up_action = QAction(_icon("up"), "Move Up", self)
        self._up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(self._up_action)

        self._down_action = QAction(_icon("down"), "Move Down", self)
        self._down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(self._down_action)

        toolbar.addSeparator()

        self._to_left_action = QAction(_icon("left"), "Move to Left Pane", self)
        self._to_left_action.setToolTip("Move selected signal to left pane")
        self._to_left_action.triggered.connect(self._move_to_left)
        toolbar.addAction(self._to_left_action)

        self._to_right_action = QAction(_icon("right"), "Move to Right Pane", self)
        self._to_right_action.setToolTip("Move selected signal to right pane")
        self._to_right_action.triggered.connect(self._move_to_right)
        toolbar.addAction(self._to_right_action)

        toolbar.addSeparator()

        self._add_to_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        self._add_to_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(self._add_to_plot_action)

        self._layout.addWidget(toolbar)

        # Dual-pane layout: left view | right view (no buttons between them)
        self._left_proxy = WatchPaneProxy(0, self)
        self._left_proxy.setSourceModel(self._model)

        self._right_proxy = WatchPaneProxy(1, self)
        self._right_proxy.setSourceModel(self._model)

        self._left_view = self._make_watch_view(self._left_proxy)
        self._right_view = self._make_watch_view(self._right_proxy)

        pane_widget = QWidget()
        pane_layout = QHBoxLayout(pane_widget)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(4)
        pane_layout.addWidget(self._left_view)
        pane_layout.addWidget(self._right_view)

        self._layout.addWidget(pane_widget)

        QTimer.singleShot(0, self._apply_default_column_sizes)

        # Mutual selection clearing: selecting in one pane clears the other
        self._pane_selecting = False
        self._left_view.selectionModel().selectionChanged.connect(
            self._on_left_pane_selected
        )
        self._right_view.selectionModel().selectionChanged.connect(
            self._on_right_pane_selected
        )

    def _make_watch_view(self, proxy: WatchPaneProxy) -> QTreeView:
        """Create and configure a watch table view backed by the given proxy."""
        view = QTreeView()
        view.setRootIsDecorated(False)
        view.setAlternatingRowColors(True)
        view.setModel(proxy)
        header = view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        view.setDragEnabled(True)
        view.setAcceptDrops(True)
        view.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        view.setDefaultDropAction(Qt.DropAction.MoveAction)
        view.setDragDropOverwriteMode(False)
        view.setDropIndicatorShown(True)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(self._on_context_menu)
        return view

    @property
    def primary_view(self):
        """The left pane tree view that receives keyboard focus."""
        return self._left_view

    @property
    def selectable_views(self):
        """Both pane views that participate in the exclusive-selection scheme."""
        return [self._left_view, self._right_view]

    def _active_view(self) -> QTreeView:
        """Return whichever pane currently has a selection, defaulting to left."""
        if self._right_view.selectionModel().hasSelection():
            return self._right_view
        return self._left_view

    def _active_proxy(self) -> WatchPaneProxy:
        """Return the proxy of the active view."""
        if self._right_view.selectionModel().hasSelection():
            return self._right_proxy
        return self._left_proxy

    def _selected_source_rows(self) -> list[int]:
        """Return selected source-model row indices from the active view."""
        view = self._active_view()
        proxy = self._active_proxy()
        rows = sorted({
            proxy.mapToSource(i).row()
            for i in view.selectionModel().selectedIndexes()
            if i.column() == 0
        })
        if not rows:
            idx = view.currentIndex()
            if idx.isValid():
                src = proxy.mapToSource(idx)
                if src.isValid():
                    rows = [src.row()]
        return rows

    def _on_left_pane_selected(self, selected, _=None):
        """Clear the right pane's selection when the left pane gains one."""
        if not self._pane_selecting and selected.indexes():
            self._pane_selecting = True
            try:
                self._right_view.clearSelection()
            finally:
                self._pane_selecting = False

    def _on_right_pane_selected(self, selected, _=None):
        """Clear the left pane's selection when the right pane gains one."""
        if not self._pane_selecting and selected.indexes():
            self._pane_selecting = True
            try:
                self._left_view.clearSelection()
            finally:
                self._pane_selecting = False

    def _on_remove(self):
        """Remove all selected signals from the watch list."""
        for row in sorted(self._selected_source_rows(), reverse=True):
            self._model.remove_watch(row)

    def _on_move_up(self):
        """Move the selected signal one row up."""
        rows = self._selected_source_rows()
        if not rows:
            return
        row = rows[0]
        self._model.move_up(row)

    def _on_move_down(self):
        """Move the selected signal one row down."""
        rows = self._selected_source_rows()
        if not rows:
            return
        row = rows[0]
        self._model.move_down(row)

    def _move_to_right(self):
        """Move selected rows from the left pane to the right pane."""
        for row in self._selected_source_rows():
            self._model.set_entry_pane(row, 1)

    def _move_to_left(self):
        """Move selected rows from the right pane to the left pane."""
        for row in self._selected_source_rows():
            self._model.set_entry_pane(row, 0)

    def _apply_default_column_sizes(self):
        """Set Dir to text-fit width and split remaining space 50/50."""
        for view in (self._left_view, self._right_view):
            header = view.header()
            dir_text_width = header.fontMetrics().horizontalAdvance("Dir")
            dir_width = dir_text_width + 24
            available = max(0, view.viewport().width())
            remaining = max(160, available - dir_width)
            name_width = remaining // 2
            value_width = remaining - name_width
            header.resizeSection(0, name_width)
            header.resizeSection(1, value_width)
            header.resizeSection(2, dir_width)

    def _on_context_menu(self, pos):
        """Show a context menu at the given viewport position."""
        source_view = self.sender()  # QTreeView that emitted the signal
        has_sel = source_view.selectionModel().hasSelection()
        self._remove_action.setEnabled(has_sel)
        self._up_action.setEnabled(has_sel)
        self._down_action.setEnabled(has_sel)
        self._add_to_plot_action.setEnabled(has_sel)
        menu = QMenu(self)
        menu.addAction(self._remove_action)
        menu.addSeparator()
        menu.addAction(self._up_action)
        menu.addAction(self._down_action)
        menu.addSeparator()
        menu.addAction(self._add_to_plot_action)
        menu.exec(source_view.viewport().mapToGlobal(pos))

    def _on_add_to_plot(self):
        """Emit :attr:`add_to_plot_requested` for every selected signal."""
        for row in self._selected_source_rows():
            if row < len(self._model.entries):
                entry = self._model.entries[row]
                self.add_to_plot_requested.emit(entry.arb_id, entry.signal_name, entry.unit)
