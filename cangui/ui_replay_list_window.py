"""Replay control panel for CTB trace playback in list tabs.

Provides controls for loading, playing, pausing, and stopping CTB trace replay,
with a settings tree for configuring replay mode, speed, bus, and loop settings.
Uses the same SettingNode/Model/Delegate pattern as the Settings tab.
"""

from PySide6.QtCore import Qt, Signal, QAbstractItemModel, QModelIndex, QSize
from PySide6.QtWidgets import (
    QToolBar, QStyledItemDelegate, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QFileDialog, QToolButton, QWidget, QSizePolicy,
)
from PySide6.QtGui import QAction

from cangui.icons import icon as _icon
from cangui.ui_tab_navigation import TabTreeView
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.worker_ctb_player import ReplayMode


class ReplaySettingNode:
    """Tree node representing a replay setting or category group."""

    def __init__(self, name: str, value=None, parent=None,
                 editor_type: str = "str", choices: list[str] | None = None,
                 min_val=None, max_val=None, key: str = ""):
        """Construct a ReplaySettingNode.

        Args:
            name: Display label shown in the Name column.
            value: Current setting value; ``None`` for category header nodes.
            parent: Parent :class:`ReplaySettingNode`, or ``None`` for root children.
            editor_type: Inline editor kind — one of ``"str"``, ``"int"``,
                ``"float"``, ``"bool"``, or ``"choice"``.
            choices: List of allowed strings for ``"choice"`` editors.
            min_val: Minimum allowed numeric value.
            max_val: Maximum allowed numeric value.
            key: Attribute name that stores this setting (e.g., ``"speed"``, ``"mode"``).
        """
        self.name = name
        self.value = value
        self.parent = parent
        self.children: list[ReplaySettingNode] = []
        self.editor_type = editor_type
        self.choices = choices or []
        self.min_val = min_val
        self.max_val = max_val
        self.key = key

    def add_child(self, node: "ReplaySettingNode"):
        """Append *node* as a child of this node."""
        node.parent = self
        self.children.append(node)

    def row(self) -> int:
        """Return the zero-based position of this node within its parent's children."""
        if self.parent:
            return self.parent.children.index(self)
        return 0


class ReplaySettingsModel(QAbstractItemModel):
    """Tree item model for replay settings with inline editing."""

    setting_changed = Signal(str, object)  # key, value

    def __init__(self, parent=None):
        """Initialize the replay settings model.

        Args:
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._root = ReplaySettingNode("root")
        self._settings = {
            "speed": 1.0,
            "max_speed": False,
            "mode": "all",
            "bus": 0,
            "loop": False,
        }
        self._build_tree()

    def _build_tree(self):
        """Build the settings tree from current values."""
        replay_group = ReplaySettingNode("Replay")
        self._root.add_child(replay_group)

        replay_group.add_child(ReplaySettingNode(
            "Speed", self._settings["speed"],
            editor_type="float", min_val=0.1, max_val=100.0, key="speed"))

        replay_group.add_child(ReplaySettingNode(
            "Max Speed", self._settings["max_speed"],
            editor_type="bool", key="max_speed"))

        replay_group.add_child(ReplaySettingNode(
            "Mode", self._settings["mode"],
            editor_type="choice", choices=["tx_only", "rx_only", "all"], key="mode"))

        replay_group.add_child(ReplaySettingNode(
            "Target Bus", self._settings["bus"],
            editor_type="int", min_val=0, max_val=16, key="bus"))

        replay_group.add_child(ReplaySettingNode(
            "Loop", self._settings["loop"],
            editor_type="bool", key="loop"))

    def index(self, row, column, parent=QModelIndex()):
        """Return a model index for the given row/column under *parent*."""
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = parent.internalPointer() if parent.isValid() else self._root
        if row < len(node.children):
            return self.createIndex(row, column, node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex):
        """Return the parent index of *index*."""
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row(), 0, node.parent)

    def rowCount(self, parent=QModelIndex()):
        """Return the number of children under *parent*."""
        if not parent.isValid():
            return len(self._root.children)
        node = parent.internalPointer()
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        """Return the fixed column count (2: Setting name and Value)."""
        return 2

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ["Setting", "Value"][section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display text for the given cell."""
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
        """Return interaction flags; the Value column is editable for leaf nodes."""
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid() and index.column() == 1:
            node = index.internalPointer()
            if node.value is not None:
                flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        """Write a new value into the model."""
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
        self._settings[node.key] = value
        self.dataChanged.emit(index, index)
        self.setting_changed.emit(node.key, value)
        return True

    def get_node(self, index: QModelIndex) -> ReplaySettingNode | None:
        """Return the :class:`ReplaySettingNode` at *index*, or ``None`` for invalid indices."""
        if index.isValid():
            return index.internalPointer()
        return None

    def get_setting(self, key: str):
        """Get a setting value by key."""
        return self._settings.get(key)


class ReplaySettingsDelegate(QStyledItemDelegate):
    """Inline editor delegate that creates the appropriate widget for each editor type."""

    def createEditor(self, parent, option, index):
        """Create an inline editor widget appropriate for the node's editor type."""
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
        """Populate the editor with the node's current value."""
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
        """Commit the editor's current value back to the model."""
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            model.setData(index, editor.value())
        else:
            super().setModelData(editor, model, index)


class ReplayListWindow(BaseDockWindow):
    """Replay control panel for CTB trace playback (list tab version).

    Provides toolbar controls for load/play/pause/stop and a settings tree
    for configuring replay settings.

    Signals:
        load_trace_requested: Emitted with file path when user loads a trace.
        replay_requested: Emitted when starting replay.
        stop_requested: Emitted when stopping replay.
        pause_requested: Emitted when pausing replay.
        resume_requested: Emitted when resuming replay.
        reset_requested: Emitted when resetting and restarting replay.
    """

    load_trace_requested = Signal(str)
    replay_requested = Signal()
    stop_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    reset_requested = Signal()

    TITLE = "Replay"

    def __init__(self, parent=None):
        """Initialize the ReplayListWindow.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)

        # Toolbar with action buttons
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))

        load_action = QAction(_icon("open"), "Load", self)
        load_action.setToolTip("Load trace file")
        load_action.triggered.connect(self._on_load)
        toolbar.addAction(load_action)

        toolbar.addSeparator()

        self._play_action = QAction(_icon("play"), "Play", self)
        self._play_action.setToolTip("Start replay [F9]")
        self._play_action.triggered.connect(self._on_play)
        toolbar.addAction(self._play_action)

        self._pause_action = QAction(_icon("pause"), "Pause", self)
        self._pause_action.setToolTip("Pause replay [P]")
        self._pause_action.setEnabled(False)
        self._pause_action.triggered.connect(self._on_pause)
        toolbar.addAction(self._pause_action)

        self._stop_action = QAction(_icon("stop"), "Stop", self)
        self._stop_action.setToolTip("Stop replay [F6]")
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        self._reset_action = QAction(_icon("refresh"), "Reset", self)
        self._reset_action.setToolTip("Restart from beginning")
        self._reset_action.setEnabled(False)
        self._reset_action.triggered.connect(self._on_reset)
        toolbar.addAction(self._reset_action)

        # Spacer to push progress to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        toolbar.addWidget(spacer)

        # Progress display
        self._progress_label = QLabel("0.0 / 0.0 s")
        self._progress_label.setStyleSheet("color: gray;")
        self._progress_label.setFixedWidth(100)
        toolbar.addWidget(self._progress_label)

        self._layout.addWidget(toolbar)

        # File info label
        self._file_label = QLabel("(no file loaded)")
        self._file_label.setStyleSheet("color: gray;")
        self._layout.addWidget(self._file_label)

        # Settings tree
        self._model = ReplaySettingsModel(self)
        self._model.setting_changed.connect(self._on_setting_changed)

        self._tree = TabTreeView()
        self._tree.setModel(self._model)
        self._tree.setAlternatingRowColors(True)
        self._tree.setItemDelegateForColumn(1, ReplaySettingsDelegate(self._tree))
        self._tree.setColumnWidth(0, 120)
        self._tree.expandAll()
        self._layout.addWidget(self._tree)

    @property
    def primary_view(self):
        """The settings tree view that receives keyboard focus."""
        return self._tree

    @property
    def speed_factor(self) -> float:
        """Return the effective speed factor."""
        speed = self._model.get_setting("speed") or 1.0
        if self._model.get_setting("max_speed"):
            return 1000.0
        return float(speed)

    @property
    def replay_mode(self) -> str:
        """Return the current replay mode."""
        return str(self._model.get_setting("mode") or "all")

    @property
    def replay_bus(self) -> int:
        """Return the target bus number."""
        return int(self._model.get_setting("bus") or 0)

    @property
    def replay_loop(self) -> bool:
        """Return whether loop mode is enabled."""
        return bool(self._model.get_setting("loop") or False)

    def set_file_info(self, path: str, frame_count: int = 0, duration: float = 0.0):
        """Update file information display.

        Args:
            path: File path or empty to clear.
            frame_count: Number of frames in trace.
            duration: Trace duration in seconds.
        """
        if path:
            from pathlib import Path
            self._file_label.setText(f"{Path(path).name} ({frame_count} frames, {duration:.1f}s)")
            self._file_label.setStyleSheet("")
            self._file_label.setToolTip(path)
        else:
            self._file_label.setText("(no file loaded)")
            self._file_label.setStyleSheet("color: gray;")
            self._file_label.setToolTip("")

    def set_replay_progress(self, current: float, total: float):
        """Update progress display.

        Args:
            current: Current playback time in seconds.
            total: Total trace duration in seconds.
        """
        self._progress_label.setText(f"{current:.1f} / {total:.1f} s")

    def set_replay_state(self, playing: bool):
        """Update button states based on replay state.

        Args:
            playing: True if replay is active.
        """
        # Disable settings while playing
        self._tree.setEnabled(not playing)

        # Update button states
        if playing:
            self._play_action.setEnabled(False)
            self._pause_action.setEnabled(True)
            self._stop_action.setEnabled(True)
            self._reset_action.setEnabled(True)
        else:
            self._play_action.setEnabled(True)
            self._pause_action.setEnabled(False)
            self._stop_action.setEnabled(False)
            self._reset_action.setEnabled(False)

    def _on_load(self):
        """Handle load button click."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Trace", "",
            "All Trace Files (*.ctb *.trc *.blf);;CTB Files (*.ctb);;TRC Files (*.trc);;BLF Files (*.blf);;All Files (*)"
        )
        if path:
            self.load_trace_requested.emit(path)

    def _on_play(self):
        """Handle play button click."""
        self.replay_requested.emit()

    def _on_pause(self):
        """Handle pause button click."""
        self.pause_requested.emit()

    def _on_stop(self):
        """Handle stop button click."""
        self.stop_requested.emit()

    def _on_reset(self):
        """Handle reset button click."""
        self.reset_requested.emit()

    def _on_setting_changed(self, key: str, value):
        """Handle setting changes (currently just for tracking)."""
        # Settings are updated in the model; they're read directly
        # when replay is requested
        pass
