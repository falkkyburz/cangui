"""Watch dock window for cangui.

Displays the most-recently-decoded value for each pinned CAN signal, backed
by :class:`~cangui.model_watch.WatchModel`.
"""
from PySide6.QtWidgets import QHeaderView, QTreeView, QToolBar
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal, QTimer

from cangui.model_watch import WatchModel
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.icons import icon as _icon


class WatchWindow(BaseDockWindow):
    """Watch panel that shows the latest decoded value for each pinned signal.

    Backed by :class:`~cangui.model_watch.WatchModel`.  Toolbar actions allow
    the user to remove signals, reorder rows, and send a signal to the Plot.

    Signals:
        remove_requested: Emitted with the row index when a signal is removed.
        add_to_plot_requested: Emitted with ``(arb_id, signal_name, unit)``
            when the user clicks "Add to Plot".
    """

    TITLE = "Watch"

    remove_requested = Signal(int)  # row
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, model: WatchModel, parent=None):
        """Build the Watch panel with its toolbar and table view.

        Args:
            model: The :class:`~cangui.model_watch.WatchModel` driving the view.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        remove_action = QAction(_icon("remove"), "Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction(_icon("trash"), "Clear All", self)
        clear_action.triggered.connect(self._model.clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Move Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Move Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        toolbar.addSeparator()

        add_to_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        add_to_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(add_to_plot_action)

        self._layout.addWidget(toolbar)

        self._view = QTreeView()
        self._view.setRootIsDecorated(False)
        self._view.setAlternatingRowColors(True)
        self._view.setModel(self._model)
        self._view.header().setStretchLastSection(True)
        self._view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._layout.addWidget(self._view)

        self._model.rowsInserted.connect(lambda *_: self._resize_columns())
        self._model.modelReset.connect(lambda *_: self._resize_columns())
        QTimer.singleShot(0, self._resize_columns)

    @property
    def primary_view(self):
        """The tree view that receives keyboard focus."""
        return self._view

    def _on_remove(self):
        """Remove the currently selected signal from the watch list."""
        index = self._view.currentIndex()
        if index.isValid():
            self._model.remove_watch(index.row())

    def _on_move_up(self):
        """Move the selected signal one row up and follow the row with the selection."""
        index = self._view.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(row)
        self._view.setCurrentIndex(self._model.index(row - 1, 0))

    def _on_move_down(self):
        """Move the selected signal one row down and follow the row with the selection."""
        index = self._view.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(row)
        self._view.setCurrentIndex(self._model.index(row + 1, 0))

    def _resize_columns(self):
        """Resize all columns except the last to fit their contents."""
        for i in range(self._model.columnCount() - 1):
            self._view.resizeColumnToContents(i)

    def _on_add_to_plot(self):
        """Emit :attr:`add_to_plot_requested` for the currently selected signal."""
        index = self._view.currentIndex()
        if not index.isValid():
            return
        entry = self._model.entries[index.row()]
        self.add_to_plot_requested.emit(entry.arb_id, entry.signal_name, entry.unit)
