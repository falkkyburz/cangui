from PySide6.QtWidgets import QHeaderView, QTreeView, QToolBar
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal

from cangui.models.watch_model import WatchModel
from cangui.ui.windows.base_dock_window import BaseDockWindow


class WatchWindow(BaseDockWindow):
    TITLE = "Watch"

    remove_requested = Signal(int)  # row
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, model: WatchModel, parent=None):
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        remove_action = QAction("Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction("Clear All", self)
        clear_action.triggered.connect(self._model.clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        add_to_plot_action = QAction("Add to Plot", self)
        add_to_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(add_to_plot_action)

        self._layout.addWidget(toolbar)

        self._view = QTreeView()
        self._view.setRootIsDecorated(False)
        self._view.setAlternatingRowColors(True)
        self._view.setModel(self._model)
        self._view.header().setStretchLastSection(True)
        self._view.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._layout.addWidget(self._view)

    @property
    def primary_view(self):
        return self._view

    def _on_remove(self):
        index = self._view.currentIndex()
        if index.isValid():
            self._model.remove_watch(index.row())

    def _on_add_to_plot(self):
        index = self._view.currentIndex()
        if not index.isValid():
            return
        entry = self._model.entries[index.row()]
        self.add_to_plot_requested.emit(entry.arb_id, entry.signal_name, entry.unit)
