"""Settings editor panel — displays AppOptions fields as an editable tree of form widgets."""

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, Signal
from PySide6.QtWidgets import (
    QWidget, QStyledItemDelegate, QLabel,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
)

from cangui.options import AppOptions
from cangui.ui_tab_navigation import TabTreeView
from cangui.ui_base_dock_window import BaseDockWindow


class SettingNode:
    """Tree node representing a single setting or a category group.

    Leaf nodes hold a setting value and editor metadata; branch nodes
    (``value is None``) act as category headers in the tree view.
    """

    def __init__(self, name: str, value=None, parent=None,
                 editor_type: str = "str", choices: list[str] | None = None,
                 min_val=None, max_val=None, category: str = "", key: str = ""):
        """Construct a SettingNode.

        Args:
            name: Display label shown in the Name column.
            value: Current setting value; ``None`` for category header nodes.
            parent: Parent :class:`SettingNode`, or ``None`` for root children.
            editor_type: Inline editor kind — one of ``"str"``, ``"int"``,
                ``"float"``, ``"bool"``, or ``"choice"``.
            choices: List of allowed strings for ``"choice"`` editors.
            min_val: Minimum allowed numeric value (used by spin-box editors).
            max_val: Maximum allowed numeric value (used by spin-box editors).
            category: Attribute name on :class:`~cangui.options.AppOptions` that
                owns this setting (e.g. ``"general"``, ``"tracer"``).
            key: Attribute name within *category* that stores this setting
                (e.g. ``"float_format"``).
        """
        self.name = name
        self.value = value
        self.parent = parent
        self.children: list[SettingNode] = []
        self.editor_type = editor_type  # "str", "int", "float", "bool", "choice"
        self.choices = choices or []
        self.min_val = min_val
        self.max_val = max_val
        self.category = category  # e.g. "general", "tracer", "plot"
        self.key = key  # e.g. "float_format", "buffer_size"

    def add_child(self, node: "SettingNode"):
        """Append *node* as a child of this node and set its parent reference.

        Args:
            node: :class:`SettingNode` to attach.
        """
        node.parent = self
        self.children.append(node)

    def row(self) -> int:
        """Return the zero-based position of this node within its parent's children.

        Returns:
            Index in ``parent.children``, or 0 for root-level nodes.
        """
        if self.parent:
            return self.parent.children.index(self)
        return 0


class SettingsModel(QAbstractItemModel):
    """Tree item model that exposes :class:`~cangui.options.AppOptions` as an editable tree.

    The tree has one level of category groups (General, Trace, Plot, Tabs) each
    containing leaf :class:`SettingNode` rows.  Editing a Value cell writes
    through to ``AppOptions`` and emits ``setting_changed``.

    Signals:
        setting_changed: Emitted after a value is committed with
            ``(category, key, new_value)``.
    """

    setting_changed = Signal(str, str, object)  # category, key, value

    def __init__(self, options: AppOptions, parent=None):
        """Initialise the model and build the setting tree from *options*.

        Args:
            options: :class:`~cangui.options.AppOptions` instance whose
                attribute values populate the leaf nodes.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._options = options
        self._root = SettingNode("root")
        self._build_tree()

    def _build_tree(self):
        """Populate ``_root`` with category and leaf :class:`SettingNode` objects.

        Reads current values from ``self._options`` and creates a two-level
        tree: category header nodes as direct children of root, and leaf nodes
        as children of their respective categories.
        """
        opts = self._options

        # General
        general = SettingNode("General")
        self._root.add_child(general)
        general.add_child(SettingNode(
            "Float format", opts.general.float_format,
            editor_type="choice", choices=["f", "e", "g"],
            category="general", key="float_format"))
        general.add_child(SettingNode(
            "Decimal places", opts.general.decimal_places,
            editor_type="int", min_val=0, max_val=10,
            category="general", key="decimal_places"))
        general.add_child(SettingNode(
            "Timestamp format", opts.general.timestamp_format,
            editor_type="choice", choices=["relative", "absolute", "epoch"],
            category="general", key="timestamp_format"))

        # Trace
        trace = SettingNode("Trace")
        self._root.add_child(trace)
        trace.add_child(SettingNode(
            "Buffer size", opts.tracer.buffer_size,
            editor_type="int", min_val=1000, max_val=10_000_000,
            category="tracer", key="buffer_size"))
        trace.add_child(SettingNode(
            "Auto-scroll", opts.tracer.auto_scroll,
            editor_type="bool",
            category="tracer", key="auto_scroll"))
        trace.add_child(SettingNode(
            "Trace format", opts.tracer.trace_format,
            editor_type="choice", choices=["trc", "blf"],
            category="tracer", key="trace_format"))

        # Plot
        plot = SettingNode("Plot")
        self._root.add_child(plot)
        plot.add_child(SettingNode(
            "Time window (s)", opts.plot.time_window,
            editor_type="float", min_val=1.0, max_val=3600.0,
            category="plot", key="time_window"))
        plot.add_child(SettingNode(
            "Max display points", opts.plot.max_display_points,
            editor_type="int", min_val=100, max_val=100_000,
            category="plot", key="max_display_points"))
        plot.add_child(SettingNode(
            "Update interval (ms)", opts.plot.update_interval_ms,
            editor_type="int", min_val=10, max_val=1000,
            category="plot", key="update_interval_ms"))

        # Tabs
        tabs_node = SettingNode("Tabs")
        self._root.add_child(tabs_node)
        for label, key in [
            ("Receive/Transmit", "receive_transmit"),
            ("Database", "database"),
            ("Trace", "trace"),
            ("Plot", "plot"),
            ("Diagnostics", "diagnostics"),
            ("Project Manager", "project_manager"),
            ("Watch", "watch"),
            ("Watch DID", "watch_did"),
            ("DTC", "dtc"),
            ("Rx Filter", "rx_filter"),
            ("Plot List", "plot_list"),
            ("Settings", "settings"),
            ("Help", "help"),
            ("Log", "log"),
        ]:
            tabs_node.add_child(SettingNode(
                label, getattr(opts.tabs, key),
                editor_type="bool", category="tabs", key=key))

    def rebuild(self):
        """Discard the current tree and rebuild it from the live AppOptions values."""
        self.beginResetModel()
        self._root = SettingNode("root")
        self._build_tree()
        self.endResetModel()

    def index(self, row, column, parent=QModelIndex()):
        """Return a model index for the given row/column under *parent*.

        Args:
            row: Zero-based child row within the parent node.
            column: Zero-based column (0 = Name, 1 = Value).
            parent: Parent index; invalid index means the root.

        Returns:
            Valid :class:`QModelIndex` pointing to the child node, or an
            invalid index if the position is out of range.
        """
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = parent.internalPointer() if parent.isValid() else self._root
        if row < len(node.children):
            return self.createIndex(row, column, node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex):
        """Return the parent index of *index*.

        Args:
            index: Child index whose parent should be found.

        Returns:
            Parent :class:`QModelIndex`, or an invalid index for top-level nodes.
        """
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row(), 0, node.parent)

    def rowCount(self, parent=QModelIndex()):
        """Return the number of children under *parent*.

        Args:
            parent: Invalid index → root children; valid index → node children.

        Returns:
            Number of child rows.
        """
        if not parent.isValid():
            return len(self._root.children)
        node = parent.internalPointer()
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed column count (2: Setting name and Value).

        Args:
            parent: Unused.

        Returns:
            Always 2.
        """
        return 2

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for the horizontal header.

        Args:
            section: 0 → "Setting", 1 → "Value".
            orientation: Only horizontal headers are populated.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Header label string, or ``None`` for unhandled orientation/role.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ["Setting", "Value"][section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display text for the given cell.

        Column 0 shows the node name; column 1 shows the string representation
        of the current value for leaf nodes.

        Args:
            index: Cell position in the model.
            role: Only ``DisplayRole`` is handled.

        Returns:
            Cell text, or ``None`` for unhandled roles or category headers.
        """
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return node.name
            if index.column() == 1 and node.value is not None:
                return str(node.value)
        return None

    def flags(self, index: QModelIndex):
        """Return interaction flags; the Value column is editable for leaf nodes.

        Args:
            index: Cell position in the model.

        Returns:
            Combined :class:`Qt.ItemFlag` value including ``ItemIsEditable``
            for editable Value cells.
        """
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid() and index.column() == 1:
            node = index.internalPointer()
            if node.value is not None:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        """Write a new value into the model, apply it to AppOptions, and emit signals.

        Converts the incoming *value* to the correct Python type based on
        ``node.editor_type`` before storing.  Returns ``False`` on type
        conversion failure or if the index targets a category header.

        Args:
            index: Cell position to update (must be the Value column).
            value: New raw value (string from delegate or typed from spin-box).
            role: Must be ``EditRole``; all other roles are rejected.

        Returns:
            ``True`` if the value was accepted and stored, ``False`` otherwise.

        Emits:
            setting_changed: With ``(category, key, new_value)`` after a
                successful write.
        """
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        node = index.internalPointer()
        if node.value is None:
            return False

        # Convert value to the right type
        try:
            if node.editor_type == "int":
                value = int(value)
            elif node.editor_type == "float":
                value = float(value)
            elif node.editor_type == "bool":
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
        except (ValueError, TypeError):
            return False

        node.value = value
        self.dataChanged.emit(index, index)

        # Apply to AppOptions
        self._apply_to_options(node)
        self.setting_changed.emit(node.category, node.key, value)
        return True

    def _apply_to_options(self, node: SettingNode):
        """Write a leaf node's value back to the corresponding AppOptions attribute.

        Args:
            node: :class:`SettingNode` whose ``category`` and ``key`` identify
                the target ``AppOptions`` attribute.
        """
        section = getattr(self._options, node.category, None)
        if section is not None and hasattr(section, node.key):
            setattr(section, node.key, node.value)

    def get_node(self, index: QModelIndex) -> SettingNode | None:
        """Return the :class:`SettingNode` at *index*, or ``None`` for invalid indices.

        Args:
            index: Model index whose internal pointer holds the node.

        Returns:
            :class:`SettingNode` for valid indices, ``None`` otherwise.
        """
        if index.isValid():
            return index.internalPointer()
        return None

    def to_dict(self) -> dict:
        """Export all settings as a flat dict for project persistence."""
        result = {}
        for group in self._root.children:
            for child in group.children:
                if child.category and child.key:
                    result.setdefault(child.category, {})[child.key] = child.value
        return result

    def from_dict(self, data: dict):
        """Load settings from a project dict, overriding current values."""
        for group in self._root.children:
            for child in group.children:
                if child.category in data and child.key in data[child.category]:
                    child.value = data[child.category][child.key]
                    self._apply_to_options(child)


class SettingsDelegate(QStyledItemDelegate):
    """Inline editor delegate that creates the appropriate widget for each editor type.

    Dispatches to QComboBox (choice/bool), QSpinBox (int), or QDoubleSpinBox
    (float) based on the ``editor_type`` of the :class:`SettingNode` at the
    edited index.
    """

    def createEditor(self, parent, option, index):
        """Create an inline editor widget appropriate for the node's editor type.

        Args:
            parent: Parent widget for the editor.
            option: Style option (unused).
            index: Model index of the cell being edited.

        Returns:
            A QComboBox, QSpinBox, or QDoubleSpinBox, or ``None`` for category
            header nodes that should not be edited.
        """
        node = index.model().get_node(index)
        if node is None or node.value is None:
            return None

        if node.editor_type == "choice":
            combo = QComboBox(parent)
            combo.addItems(node.choices)
            return combo
        elif node.editor_type == "int":
            spin = QSpinBox(parent)
            if node.min_val is not None:
                spin.setMinimum(node.min_val)
            if node.max_val is not None:
                spin.setMaximum(node.max_val)
            return spin
        elif node.editor_type == "float":
            spin = QDoubleSpinBox(parent)
            spin.setDecimals(1)
            if node.min_val is not None:
                spin.setMinimum(node.min_val)
            if node.max_val is not None:
                spin.setMaximum(node.max_val)
            return spin
        elif node.editor_type == "bool":
            combo = QComboBox(parent)
            combo.addItems(["True", "False"])
            return combo
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        """Populate the editor with the node's current value.

        Args:
            editor: Editor widget created by :meth:`createEditor`.
            index: Model index whose node value should be reflected.
        """
        node = index.model().get_node(index)
        if node is None:
            return
        if isinstance(editor, QComboBox):
            text = str(node.value)
            idx = editor.findText(text)
            if idx >= 0:
                editor.setCurrentIndex(idx)
        elif isinstance(editor, QSpinBox):
            editor.setValue(int(node.value))
        elif isinstance(editor, QDoubleSpinBox):
            editor.setValue(float(node.value))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        """Commit the editor's current value back to the model.

        Args:
            editor: Editor widget whose value should be committed.
            model: The item model owning the cell.
            index: Model index of the cell to update.
        """
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            model.setData(index, editor.value())
        else:
            super().setModelData(editor, model, index)


class SettingsWindow(BaseDockWindow):
    """Settings list view showing editable application settings."""

    TITLE = "Settings"

    setting_changed = Signal(str, str, object)  # category, key, value

    def __init__(self, options: AppOptions, parent=None):
        """Build the SettingsWindow with an expanded tree view of editable options.

        Args:
            options: :class:`~cangui.options.AppOptions` instance that provides
                the initial values and receives writes through the delegate.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._options = options

        info = QLabel(
            "Changes apply immediately. Global defaults are saved to "
            "~/.config/cangui/options.json. The active project stores "
            "its own overrides, which take effect when the project is loaded."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; font-style: italic;")
        self._layout.addWidget(info)

        self._model = SettingsModel(options, self)
        self._model.setting_changed.connect(self.setting_changed)

        self._tree = TabTreeView()
        self._tree.setModel(self._model)
        self._tree.setAlternatingRowColors(True)
        self._tree.setItemDelegateForColumn(1, SettingsDelegate(self._tree))
        self._tree.setColumnWidth(0, 180)
        self._tree.expandAll()
        self._layout.addWidget(self._tree)

    @property
    def primary_view(self):
        """The settings tree view that receives keyboard focus."""
        return self._tree

    @property
    def model(self) -> SettingsModel:
        """The :class:`SettingsModel` backing this window."""
        return self._model

    def apply_project_settings(self, settings: dict):
        """Load settings from project data, overriding defaults."""
        if settings:
            self._model.from_dict(settings)

    def collect_settings(self) -> dict:
        """Return settings dict for project persistence."""
        return self._model.to_dict()
