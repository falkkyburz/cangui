from PySide6.QtWidgets import QHeaderView, QTreeView, QToolBar
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal

from cangui.model_watch import WatchModel
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.icons import icon as _icon


class WatchWindow(BaseDockWindow):
    TITLE = "Watch"

    remove_requested = Signal(int)  # row
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, model: WatchModel, parent=None):
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

    @property
    def primary_view(self):
        return self._view

    def _on_remove(self):
        index = self._view.currentIndex()
        if index.isValid():
            self._model.remove_watch(index.row())

    def _on_move_up(self):
        index = self._view.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(row)
        self._view.setCurrentIndex(self._model.index(row - 1, 0))

    def _on_move_down(self):
        index = self._view.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(row)
        self._view.setCurrentIndex(self._model.index(row + 1, 0))

    def _on_add_to_plot(self):
        index = self._view.currentIndex()
        if not index.isValid():
            return
        entry = self._model.entries[index.row()]
        self.add_to_plot_requested.emit(entry.arb_id, entry.signal_name, entry.unit)
