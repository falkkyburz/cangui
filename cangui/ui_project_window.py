from PySide6.QtWidgets import QTreeView, QToolBar, QHeaderView
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal

from cangui.model_project import ProjectModel
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.icons import icon as _icon


class ProjectWindow(BaseDockWindow):
    TITLE = "Project Manager"

    new_requested = Signal()
    load_requested = Signal()
    save_requested = Signal()
    save_as_requested = Signal()
    file_remove_requested = Signal(str, str)  # (path, category)

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        new_action = QAction(_icon("new"), "New", self)
        new_action.triggered.connect(self.new_requested)
        toolbar.addAction(new_action)

        load_action = QAction(_icon("open"), "Load", self)
        load_action.triggered.connect(self.load_requested)
        toolbar.addAction(load_action)

        save_action = QAction(_icon("save"), "Save [Ctrl+S]", self)
        save_action.triggered.connect(self.save_requested)
        toolbar.addAction(save_action)

        save_as_action = QAction(_icon("save-as"), "Save As", self)
        save_as_action.triggered.connect(self.save_as_requested)
        toolbar.addAction(save_as_action)

        toolbar.addSeparator()

        remove_action = QAction(_icon("trash"), "Remove File", self)
        remove_action.setToolTip("Remove selected file from project")
        remove_action.triggered.connect(self._remove_selected)
        toolbar.addAction(remove_action)

        self._layout.addWidget(toolbar)

        self._view = QTreeView()
        self._view.setModel(self._model)

        header = self._view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 200)
        header.resizeSection(1, 70)

        self._view.expandAll()
        self._layout.addWidget(self._view)

    def _remove_selected(self):
        idx = self._view.currentIndex()
        node = self._model.get_node(idx)
        if node and node.path and node.category:
            self.file_remove_requested.emit(node.path, node.category)

    @property
    def primary_view(self):
        return self._view

    def refresh(self):
        self._model.refresh()
        self._view.expandAll()
