"""Qt tree model for the project panel showing files associated with a project.

The tree has a fixed three-level structure:

    <project file>
    ├── Databases
    │   └── <db files>
    ├── Traces
    │   └── <trace files>
    ├── Plot Traces
    │   └── <plot files>
    ├── Script Plugin
    │   └── <script file>
    └── Seed-Key
        └── <seed-key file>

Columns:
    0: Name — filename for leaf nodes, category label for group nodes.
    1: Size — on-disk file size (updated every 5 s by a QTimer).
    2: Status — "File not found" (in red) when the path does not exist.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QTimer
from PySide6.QtGui import QColor, QIcon

from cangui.project import Project


class ProjectNode:
    """Internal tree node used by ProjectModel.

    Each node represents either the invisible root, the project file, a
    category group ("Databases", "Traces", etc.), or a leaf file entry.

    Attributes:
        name: Display label for the node.
        path: Absolute filesystem path for leaf and project nodes; empty string
            for group/root nodes.
        category: File type tag used by the UI ("db", "trace", "plot",
            "script", "seedkey", or "" for group/root nodes).
        parent: Parent node reference; None for the invisible root.
        children: Ordered list of child nodes.
    """

    def __init__(self, name: str, path: str = "", category: str = "",
                 parent: "ProjectNode | None" = None):
        """Initialise a project tree node.

        Args:
            name: Human-readable display label.
            path: Absolute path to the file this node represents, or empty
                string for group/root nodes.
            category: File type tag ("db", "trace", "plot", "script",
                "seedkey", or "").
            parent: Parent node, or None for root.
        """
        self.name = name
        self.path = path
        self.category = category  # "db", "trace", "plot", or "" for group/root nodes
        self.parent = parent
        self.children: list[ProjectNode] = []

    def add_child(self, node: "ProjectNode"):
        """Append a child node and set its parent reference.

        Args:
            node: The child node to append.
        """
        node.parent = self
        self.children.append(node)

    def remove_child(self, row: int):
        """Remove the child at the given index, if in bounds.

        Args:
            row: Zero-based index of the child to remove.
        """
        if 0 <= row < len(self.children):
            self.children.pop(row)

    def row(self) -> int:
        """Return this node's index within its parent's children list.

        Returns:
            The zero-based position in the parent's children list, or 0 if
            this node has no parent.
        """
        if self.parent:
            return self.parent.children.index(self)
        return 0


class ProjectModel(QAbstractItemModel):
    """Tree model that represents a cangui Project as a file browser.

    The tree is rebuilt from scratch whenever ``refresh()`` is called (e.g.
    after loading a new project). A QTimer fires every 5 seconds to refresh the
    Size and Status columns without rebuilding the whole tree.

    Column layout:
        0: Name — project filename at the top level, category labels for group
           nodes, and filenames for leaf nodes.
        1: Size — human-readable on-disk size for nodes that have a path.
        2: Status — "File not found" when the path does not exist on disk.
    """

    def __init__(self, project: Project, parent=None):
        """Initialise the model and start the periodic size-refresh timer.

        Args:
            project: The Project instance whose file lists this model mirrors.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._project = project
        self._root = ProjectNode("root")
        self._rebuild()

        self._size_timer = QTimer(self)
        self._size_timer.setInterval(5000)
        self._size_timer.timeout.connect(self._refresh_sizes)
        self._size_timer.start()

    def _rebuild(self):
        """Reconstruct the entire node tree from the current project data.

        Called on initialisation and whenever the project changes. Emits
        modelReset via beginResetModel/endResetModel so that attached views
        discard all cached data.
        """
        self.beginResetModel()
        self._root = ProjectNode("root")
        proj_node = ProjectNode(
            self._project.name,
            path=str(self._project.path) if self._project.path else "",
        )
        self._root.add_child(proj_node)

        # Database files
        has_db_editor = (self._project.data.database_editor_file
                         and self._project.path)
        if self._project.data.database_files or has_db_editor:
            db_group = ProjectNode("Databases")
            proj_node.add_child(db_group)
            for f in self._project.data.database_files:
                db_group.add_child(ProjectNode(Path(f).name, path=f, category="db"))
            if has_db_editor:
                db_editor_path = str(self._project.path.parent
                                     / self._project.data.database_editor_file)
                db_group.add_child(
                    ProjectNode(self._project.data.database_editor_file,
                                path=db_editor_path))

        # Trace files
        if self._project.data.trace_files:
            trace_group = ProjectNode("Traces")
            proj_node.add_child(trace_group)
            for f in self._project.data.trace_files:
                trace_group.add_child(ProjectNode(Path(f).name, path=f, category="trace"))

        # Plot trace files
        if self._project.data.plot_files:
            plot_group = ProjectNode("Plot Traces")
            proj_node.add_child(plot_group)
            for f in self._project.data.plot_files:
                plot_group.add_child(ProjectNode(Path(f).name, path=f, category="plot"))

        # Script plugin
        if self._project.data.script_plugin_file:
            script_group = ProjectNode("Script Plugin")
            proj_node.add_child(script_group)
            f = self._project.data.script_plugin_file
            script_group.add_child(ProjectNode(Path(f).name, path=f, category="script"))

        # Seed-Key plugin
        if self._project.data.seedkey_file:
            sk_group = ProjectNode("Seed-Key")
            proj_node.add_child(sk_group)
            f = self._project.data.seedkey_file
            sk_group.add_child(ProjectNode(Path(f).name, path=f, category="seedkey"))

        self.endResetModel()

    def refresh(self):
        """Rebuild the tree from the current project data.

        Call this after the project has been loaded or modified (e.g. files
        added or removed).
        """
        self._rebuild()

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        """Return the QModelIndex for the given row/column under parent.

        Args:
            row: Child row within the parent node.
            column: Column number (0–2).
            parent: The parent index; an invalid index refers to the root.

        Returns:
            A valid QModelIndex wrapping the corresponding ProjectNode, or an
            invalid index when the position is out of range.
        """
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            node = self._root
        else:
            node = parent.internalPointer()
        if row < len(node.children):
            return self.createIndex(row, column, node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        """Return the parent index of the given index.

        Args:
            index: The child index whose parent is requested.

        Returns:
            The parent QModelIndex, or an invalid index for top-level items
            (direct children of the invisible root).
        """
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row(), 0, node.parent)

    def rowCount(self, parent=QModelIndex()) -> int:
        """Return the number of children under the given parent index.

        Args:
            parent: The parent index; an invalid index refers to the root.

        Returns:
            Number of child nodes.
        """
        if not parent.isValid():
            return len(self._root.children)
        node = parent.internalPointer()
        return len(node.children)

    def columnCount(self, parent=QModelIndex()) -> int:
        """Return the fixed number of columns (3: Name, Size, Status).

        Args:
            parent: Unused.

        Returns:
            Always 3.
        """
        return 3

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for the horizontal header.

        Args:
            section: Column index (0 = Name, 1 = Size, 2 = Status).
            orientation: Qt.Orientation; only Horizontal is handled.
            role: ItemDataRole; only DisplayRole returns a value.

        Returns:
            The column header string, or None for unsupported roles/orientations.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == 0:
                return "Name"
            if section == 1:
                return "Size"
            if section == 2:
                return "Status"
        return None

    @staticmethod
    def _format_size(size: int) -> str:
        """Format a byte count as a human-readable string.

        Args:
            size: File size in bytes.

        Returns:
            A string such as "4.2 KB", "1.5 MB", "2.1 GB", or "512 B".
        """
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display text or foreground colour for a cell.

        Column mapping for DisplayRole:
            0: Node name (filename for leaf/project nodes).
            1: Human-readable file size (only for nodes with a path).
            2: "File not found" if the path does not exist on disk.

        Column mapping for ForegroundRole:
            2: Dark red (#C62828) when the file does not exist.

        Args:
            index: The model index identifying the cell.
            role: The data role being queried.

        Returns:
            A string for DisplayRole, a QColor for ForegroundRole, or None
            for unsupported roles or invalid indices.
        """
        if not index.isValid():
            return None
        node = index.internalPointer()
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                # Show filename with extension for the project node
                if node.path and node.parent is self._root:
                    return Path(node.path).name
                return node.name
            if col == 1 and node.path:
                p = Path(node.path)
                if p.is_file():
                    return self._format_size(p.stat().st_size)
            if col == 2 and node.path:
                if not Path(node.path).is_file():
                    return "File not found"

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 2 and node.path and not Path(node.path).is_file():
                return QColor("#C62828")

        return None

    def flags(self, index: QModelIndex):
        """Return item flags — all items are enabled and selectable, none editable.

        Args:
            index: The model index whose flags are requested.

        Returns:
            ItemIsEnabled | ItemIsSelectable for every valid index.
        """
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def _refresh_sizes(self):
        """Emit dataChanged for the Size and Status columns so views update them.

        Called every 5 seconds by the internal QTimer. Only columns 1 and 2
        are invalidated; column 0 (Name) is stable between refreshes.

        Emits:
            dataChanged: For every node cell in columns 1 and 2 that has a
                filesystem path.
        """
        self._emit_column_changed(self._root, QModelIndex())

    def _emit_column_changed(self, node: ProjectNode, parent: QModelIndex):
        """Recursively emit dataChanged for Size/Status columns of all nodes.

        Args:
            node: The current node whose children are iterated.
            parent: The QModelIndex corresponding to ``node``.

        Emits:
            dataChanged: For columns 1 and 2 of each child that has a path, and
                recursively for their descendants.
        """
        for row, child in enumerate(node.children):
            if child.path:
                for col in (1, 2):
                    idx = self.index(row, col, parent)
                    self.dataChanged.emit(idx, idx)
            if child.children:
                child_parent = self.index(row, 0, parent)
                self._emit_column_changed(child, child_parent)

    def get_node(self, index: QModelIndex) -> ProjectNode | None:
        """Return the ProjectNode associated with a model index.

        Args:
            index: A model index obtained from this model.

        Returns:
            The underlying ProjectNode, or None if the index is invalid.
        """
        if index.isValid():
            return index.internalPointer()
        return None

    def get_file_path(self, index: QModelIndex) -> str:
        """Return the filesystem path stored in a node, if any.

        Args:
            index: A model index obtained from this model.

        Returns:
            The absolute path string for the node, or an empty string if the
            node has no associated path or the index is invalid.
        """
        node = self.get_node(index)
        if node and node.path:
            return node.path
        return ""
