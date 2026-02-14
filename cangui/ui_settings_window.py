from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeView, QStyledItemDelegate,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
)

from cangui.options import AppOptions


class SettingNode:
    def __init__(self, name: str, value=None, parent=None,
                 editor_type: str = "str", choices: list[str] | None = None,
                 min_val=None, max_val=None, category: str = "", key: str = ""):
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
        node.parent = self
        self.children.append(node)

    def row(self) -> int:
        if self.parent:
            return self.parent.children.index(self)
        return 0


class SettingsModel(QAbstractItemModel):
    setting_changed = Signal(str, str, object)  # category, key, value

    def __init__(self, options: AppOptions, parent=None):
        super().__init__(parent)
        self._options = options
        self._root = SettingNode("root")
        self._build_tree()

    def _build_tree(self):
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

    def rebuild(self):
        self.beginResetModel()
        self._root = SettingNode("root")
        self._build_tree()
        self.endResetModel()

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = parent.internalPointer() if parent.isValid() else self._root
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
            return ["Setting", "Value"][section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
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
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid() and index.column() == 1:
            node = index.internalPointer()
            if node.value is not None:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
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
        section = getattr(self._options, node.category, None)
        if section is not None and hasattr(section, node.key):
            setattr(section, node.key, node.value)

    def get_node(self, index: QModelIndex) -> SettingNode | None:
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
    def createEditor(self, parent, option, index):
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
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            model.setData(index, editor.value())
        else:
            super().setModelData(editor, model, index)


class SettingsWindow(QWidget):
    """Settings list view showing editable application settings."""

    TITLE = "Settings"

    setting_changed = Signal(str, str, object)  # category, key, value

    def __init__(self, options: AppOptions, parent=None):
        super().__init__(parent)
        self._options = options

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._model = SettingsModel(options, self)
        self._model.setting_changed.connect(self.setting_changed)

        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setAlternatingRowColors(True)
        self._tree.setItemDelegateForColumn(1, SettingsDelegate(self._tree))
        self._tree.setColumnWidth(0, 180)
        self._tree.expandAll()
        layout.addWidget(self._tree)

    @property
    def primary_view(self):
        return self._tree

    @property
    def model(self) -> SettingsModel:
        return self._model

    def apply_project_settings(self, settings: dict):
        """Load settings from project data, overriding defaults."""
        if settings:
            self._model.from_dict(settings)

    def collect_settings(self) -> dict:
        """Return settings dict for project persistence."""
        return self._model.to_dict()
