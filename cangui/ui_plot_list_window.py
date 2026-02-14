from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolBar
from PySide6.QtGui import QAction

from pyqtgraph.parametertree import ParameterTree, Parameter


# Predefined colors for plot curves
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


class PlotListWindow(QWidget):
    """Plot list tab showing plotted signals with editable properties."""

    TITLE = "Plot List"

    signal_added = Signal(int, str, str, str, int)  # arb_id, signal_name, unit, color, width
    signal_removed = Signal(int, str)  # arb_id, signal_name
    signal_settings_changed = Signal(int, str, dict)  # arb_id, signal_name, {color, width, visible}
    all_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color_index = 0
        self._signal_params: dict[tuple[int, str], Parameter] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        remove_action = QAction("Remove Selected", self)
        remove_action.triggered.connect(self._on_remove_selected)
        toolbar.addAction(remove_action)

        clear_action = QAction("Clear All", self)
        clear_action.triggered.connect(self._on_clear_all)
        toolbar.addAction(clear_action)

        layout.addWidget(toolbar)

        # ParameterTree
        self._params = Parameter.create(name="Signals", type="group", children=[])
        self._tree = ParameterTree(showHeader=False)
        self._tree.setParameters(self._params, showTop=False)
        layout.addWidget(self._tree)

    @property
    def primary_view(self):
        return self._tree

    def _next_color(self) -> str:
        color = COLORS[self._color_index % len(COLORS)]
        self._color_index += 1
        return color

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        key = (arb_id, signal_name)
        if key in self._signal_params:
            return

        color = self._next_color()
        width = 2
        label = f"{signal_name} (0x{arb_id:03X})"

        group = Parameter.create(name=label, type="group", children=[
            {"name": "Color", "type": "color", "value": color},
            {"name": "Width", "type": "int", "value": width, "limits": (1, 5)},
            {"name": "Visible", "type": "bool", "value": True},
            {"name": "Unit", "type": "str", "value": unit, "readonly": True},
        ])
        # Store arb_id and signal_name on the group for retrieval
        group.arb_id = arb_id
        group.signal_name = signal_name

        self._params.addChild(group)
        self._signal_params[key] = group

        # Connect change signals
        group.child("Color").sigValueChanged.connect(
            lambda _, v, k=key: self._on_param_changed(k))
        group.child("Width").sigValueChanged.connect(
            lambda _, v, k=key: self._on_param_changed(k))
        group.child("Visible").sigValueChanged.connect(
            lambda _, v, k=key: self._on_param_changed(k))

        self.signal_added.emit(arb_id, signal_name, unit, color, width)

    def remove_signal(self, arb_id: int, signal_name: str):
        key = (arb_id, signal_name)
        group = self._signal_params.pop(key, None)
        if group is not None:
            self._params.removeChild(group)
            self.signal_removed.emit(arb_id, signal_name)

    def _on_param_changed(self, key: tuple[int, str]):
        group = self._signal_params.get(key)
        if group is None:
            return
        settings = {
            "color": group.child("Color").value().name(),
            "width": group.child("Width").value(),
            "visible": group.child("Visible").value(),
        }
        self.signal_settings_changed.emit(key[0], key[1], settings)

    def _on_remove_selected(self):
        selected = self._tree.selectedItems()
        if not selected:
            return
        # Find the top-level parameter group for the selected item
        for item in selected:
            param = self._tree.itemWidget(item, 0)
            # Walk up to find the group parameter
            for key, group in list(self._signal_params.items()):
                # Check if this is the group or a child of the group
                if self._params.hasChild(group):
                    self.remove_signal(key[0], key[1])
                    break

    def _on_clear_all(self):
        for key in list(self._signal_params.keys()):
            self.remove_signal(key[0], key[1])
        self._color_index = 0
        self.all_cleared.emit()

    def get_signal_settings(self, arb_id: int, signal_name: str) -> dict | None:
        group = self._signal_params.get((arb_id, signal_name))
        if group is None:
            return None
        return {
            "color": group.child("Color").value().name(),
            "width": group.child("Width").value(),
            "visible": group.child("Visible").value(),
        }
