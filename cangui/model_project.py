from pathlib import Path

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QTimer
from PySide6.QtGui import QIcon

from cangui.project import Project


class ProjectNode:
    def __init__(self, name: str, path: str = "", parent: "ProjectNode | None" = None):
        self.name = name
        self.path = path
        self.parent = parent
        self.children: list[ProjectNode] = []

    def add_child(self, node: "ProjectNode"):
        node.parent = self
        self.children.append(node)

    def remove_child(self, row: int):
        if 0 <= row < len(self.children):
            self.children.pop(row)

    def row(self) -> int:
        if self.parent:
            return self.parent.children.index(self)
        return 0


class ProjectModel(QAbstractItemModel):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self._project = project
        self._root = ProjectNode("root")
        self._rebuild()

        self._size_timer = QTimer(self)
        self._size_timer.setInterval(5000)
        self._size_timer.timeout.connect(self._refresh_sizes)
        self._size_timer.start()

    def _rebuild(self):
        self.beginResetModel()
        self._root = ProjectNode("root")
        proj_node = ProjectNode(self._project.name)
        self._root.add_child(proj_node)

        # Database files
        if self._project.data.database_files:
            db_group = ProjectNode("Databases")
            proj_node.add_child(db_group)
            for f in self._project.data.database_files:
                db_group.add_child(ProjectNode(Path(f).name, path=f))

        # Trace files
        if self._project.data.trace_files:
            trace_group = ProjectNode("Traces")
            proj_node.add_child(trace_group)
            for f in self._project.data.trace_files:
                trace_group.add_child(ProjectNode(Path(f).name, path=f))

        # Plot trace files
        if self._project.data.plot_files:
            plot_group = ProjectNode("Plot Traces")
            proj_node.add_child(plot_group)
            for f in self._project.data.plot_files:
                plot_group.add_child(ProjectNode(Path(f).name, path=f))

        self.endResetModel()

    def refresh(self):
        self._rebuild()

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            node = self._root
        else:
            node = parent.internalPointer()
        if row < len(node.children):
            return self.createIndex(row, column, node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row(), 0, node.parent)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._root.children)
        node = parent.internalPointer()
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return 2

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == 0:
                return "Name"
            if section == 1:
                return "Size"
        return None

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return node.name
            if index.column() == 1 and node.path:
                p = Path(node.path)
                if p.is_file():
                    return self._format_size(p.stat().st_size)
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def _refresh_sizes(self):
        """Emit dataChanged for the size column so file sizes update."""
        self._emit_size_changed(self._root, QModelIndex())

    def _emit_size_changed(self, node: ProjectNode, parent: QModelIndex):
        for row, child in enumerate(node.children):
            if child.path:
                idx = self.index(row, 1, parent)
                self.dataChanged.emit(idx, idx)
            if child.children:
                child_parent = self.index(row, 0, parent)
                self._emit_size_changed(child, child_parent)

    def get_node(self, index: QModelIndex) -> ProjectNode | None:
        if index.isValid():
            return index.internalPointer()
        return None

    def get_file_path(self, index: QModelIndex) -> str:
        """Get the database file path of a node, if it has one."""
        node = self.get_node(index)
        if node and node.path:
            return node.path
        return ""
