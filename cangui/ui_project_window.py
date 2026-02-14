from PySide6.QtWidgets import QTreeView, QToolBar
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal

from cangui.model_project import ProjectModel
from cangui.ui_base_dock_window import BaseDockWindow


class ProjectWindow(BaseDockWindow):
    TITLE = "Project Manager"

    add_file_requested = Signal()
    remove_file_requested = Signal(str)  # file path
    new_requested = Signal()
    load_requested = Signal()
    save_requested = Signal()
    save_as_requested = Signal()

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        new_action = QAction("New", self)
        new_action.triggered.connect(self.new_requested)
        toolbar.addAction(new_action)

        load_action = QAction("Load", self)
        load_action.triggered.connect(self.load_requested)
        toolbar.addAction(load_action)

        save_action = QAction("Save [Ctrl+S]", self)
        save_action.triggered.connect(self.save_requested)
        toolbar.addAction(save_action)

        save_as_action = QAction("Save As", self)
        save_as_action.triggered.connect(self.save_as_requested)
        toolbar.addAction(save_as_action)

        toolbar.addSeparator()

        add_action = QAction("Add", self)
        add_action.triggered.connect(self.add_file_requested)
        toolbar.addAction(add_action)

        remove_action = QAction("Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        self._layout.addWidget(toolbar)

        self._view = QTreeView()
        self._view.setModel(self._model)
        self._view.header().setStretchLastSection(False)
        self._view.header().resizeSection(0, 200)
        self._view.header().resizeSection(1, 70)
        self._view.expandAll()
        self._layout.addWidget(self._view)

    @property
    def primary_view(self):
        return self._view

    def _on_remove(self):
        index = self._view.currentIndex()
        path = self._model.get_file_path(index)
        if path:
            self.remove_file_requested.emit(path)

    def refresh(self):
        self._model.refresh()
        self._view.expandAll()
