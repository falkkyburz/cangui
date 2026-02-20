"""Project Manager dock window for cangui.

Shows the project tree (databases, traces, plugins) and exposes toolbar
actions for creating, loading, saving, and importing project files.
"""
from PySide6.QtWidgets import QApplication, QTreeView, QToolBar, QHeaderView, QFileDialog
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal

from cangui.model_project import ProjectModel
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.icons import icon as _icon


class ProjectWindow(BaseDockWindow):
    """Project Manager panel showing the current project's file tree.

    Displays databases, traces, plot files, and plugins in a QTreeView backed
    by :class:`~cangui.model_project.ProjectModel`.  Toolbar actions emit
    signals that :class:`~cangui.ui_main_window.MainWindow` connects to its
    project-lifecycle methods.

    Signals:
        new_requested: User clicked "New".
        load_requested: User clicked "Load".
        save_requested: User clicked "Save".
        save_as_requested: User clicked "Save As".
        file_remove_requested: Emitted with ``(path, category)`` when the
            user removes a file from the project tree.
        import_file_requested: Emitted with the absolute path when the user
            selects a file to import via the "Import" dialog.
    """

    TITLE = "Project Manager"

    new_requested = Signal()
    load_requested = Signal()
    save_requested = Signal()
    save_as_requested = Signal()
    file_remove_requested = Signal(str, str)  # (path, category)
    import_file_requested = Signal(str)  # path

    def __init__(self, model: ProjectModel, parent=None):
        """Build the Project Manager panel with its toolbar and tree view.

        Args:
            model: The :class:`~cangui.model_project.ProjectModel` that backs
                the tree view.
            parent: Optional Qt parent widget.
        """
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

        import_action = QAction(_icon("import"), "Import", self)
        import_action.setToolTip(
            "Import a file into the project\n"
            "Supported: .script.py  .seedkey.py  .dbc  .db.json"
        )
        import_action.triggered.connect(self._browse_import)
        toolbar.addAction(import_action)

        toolbar.addSeparator()

        remove_action = QAction(_icon("trash"), "Remove File", self)
        remove_action.setToolTip("Remove selected file from project")
        remove_action.triggered.connect(self._remove_selected)
        toolbar.addAction(remove_action)

        copy_path_action = QAction(_icon("copy"), "Copy Path", self)
        copy_path_action.setToolTip("Copy full path of selected file to clipboard")
        copy_path_action.triggered.connect(self._copy_path)
        toolbar.addAction(copy_path_action)

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
        self._view.collapsed.connect(self._prevent_collapse)
        self._layout.addWidget(self._view)

    def _prevent_collapse(self, index):
        """Re-expand top-level category nodes so they cannot be collapsed."""
        if not index.parent().isValid():
            self._view.expand(index)

    def _browse_import(self):
        """Open a file dialog and emit :attr:`import_file_requested` with the chosen path."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import File", "",
            "Supported Files (*.script.py *.seedkey.py *.dbc *.db.json *.odx *.pdx *.odx-d);;"
            "Script Plugin (*.script.py);;"
            "Seed-Key Plugin (*.seedkey.py);;"
            "DBC Database (*.dbc);;"
            "Database JSON (*.db.json);;"
            "ODX/PDX Database (*.odx *.pdx *.odx-d);;"
            "All Files (*)"
        )
        if path:
            self.import_file_requested.emit(path)

    def _remove_selected(self):
        """Emit :attr:`file_remove_requested` for the currently selected file node."""
        idx = self._view.currentIndex()
        node = self._model.get_node(idx)
        if node and node.path and node.category:
            self.file_remove_requested.emit(node.path, node.category)

    def _copy_path(self):
        """Copy the absolute path of the selected file node to the clipboard."""
        idx = self._view.currentIndex()
        node = self._model.get_node(idx)
        if node and node.path:
            QApplication.clipboard().setText(node.path)

    @property
    def primary_view(self):
        """The tree view that receives keyboard focus."""
        return self._view

    def refresh(self):
        """Reload the project model and re-expand all category nodes."""
        self._model.refresh()
        self._view.expandAll()
