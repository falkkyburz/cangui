from PySide6.QtCore import Qt, Signal, QModelIndex, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QTableView, QHeaderView,
    QColorDialog, QSpinBox, QStyledItemDelegate,
)
from PySide6.QtGui import QAction, QColor

from cangui.model_plot_list import PlotListModel, COL_COLOR, COL_WIDTH
from cangui.icons import icon as _icon


# Predefined colors for plot curves
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


class _ColorDelegate(QStyledItemDelegate):
    """Paints a filled color swatch; editing is handled by the view's doubleClicked signal."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        color_str = index.data(Qt.ItemDataRole.DisplayRole)
        if color_str:
            color = QColor(color_str)
            if color.isValid():
                rect = option.rect.adjusted(4, 4, -4, -4)
                painter.fillRect(rect, color)
                painter.setPen(QColor("#888888"))
                painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def createEditor(self, parent, option, index):
        return None  # handled via doubleClicked signal in PlotListWindow


class _WidthDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        sb = QSpinBox(parent)
        sb.setRange(1, 5)
        return sb

    def setEditorData(self, editor, index):
        val = index.data(Qt.ItemDataRole.EditRole)
        editor.setValue(val if isinstance(val, int) else 2)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class PlotListWindow(QWidget):
    """Plot list tab — shows plotted signals with editable style properties."""

    TITLE = "Plot List"

    signal_added = Signal(int, str, str, str, int)    # arb_id, signal_name, unit, color, width
    signal_removed = Signal(int, str)                 # arb_id, signal_name
    signal_settings_changed = Signal(int, str, dict)  # arb_id, signal_name, {color,width,visible}
    all_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color_index = 0
        self._model = PlotListModel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        remove_action = QAction(_icon("remove"), "Remove Selected", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        clear_action = QAction(_icon("trash"), "Clear All", self)
        clear_action.triggered.connect(self._on_clear_all)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Move Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Move Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        layout.addWidget(toolbar)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setItemDelegateForColumn(COL_COLOR, _ColorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_WIDTH, _WidthDelegate(self._table))
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        self._model.rowsInserted.connect(lambda *_: self._resize_columns())
        self._model.modelReset.connect(lambda *_: self._resize_columns())
        self._model.dataChanged.connect(self._on_model_data_changed)
        QTimer.singleShot(0, self._resize_columns)

    def _resize_columns(self):
        for i in range(self._model.columnCount() - 1):
            self._table.resizeColumnToContents(i)

    @property
    def primary_view(self):
        return self._table

    def set_decoder(self, decoder):
        self._model.set_decoder(decoder)

    def on_message(self, msg):
        self._model.on_message(msg)

    def on_messages(self, messages):
        self._model.on_messages(messages)

    # -- Color picking via double-click --

    def _on_double_click(self, index: QModelIndex):
        if index.column() == COL_COLOR:
            current = QColor(index.data(Qt.ItemDataRole.EditRole) or "#ffffff")
            color = QColorDialog.getColor(current, self, "Select Curve Color")
            if color.isValid():
                self._model.setData(index, color.name(), Qt.ItemDataRole.EditRole)

    # -- Model change → signal_settings_changed --

    def _on_model_data_changed(self, top_left: QModelIndex, bottom_right: QModelIndex):
        settings_cols = {COL_COLOR, COL_WIDTH, 4}  # 4 = COL_VISIBLE
        changed_cols = set(range(top_left.column(), bottom_right.column() + 1))
        if not (settings_cols & changed_cols):
            return
        for row in range(top_left.row(), bottom_right.row() + 1):
            if row >= len(self._model.entries):
                continue
            entry = self._model.entries[row]
            self.signal_settings_changed.emit(entry.arb_id, entry.signal_name, {
                "color": entry.color,
                "width": entry.width,
                "visible": entry.visible,
            })

    # -- Toolbar handlers --

    def _on_remove(self):
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        if row < len(self._model.entries):
            entry = self._model.entries[row]
            self._model.remove_entry(row)
            self.signal_removed.emit(entry.arb_id, entry.signal_name)

    def _on_clear_all(self):
        for entry in list(self._model.entries):
            self.signal_removed.emit(entry.arb_id, entry.signal_name)
        self._model.clear()
        self._color_index = 0
        self.all_cleared.emit()

    def _on_move_up(self):
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_up(row)
        self._table.setCurrentIndex(self._model.index(row - 1, 0))

    def _on_move_down(self):
        index = self._table.currentIndex()
        if not index.isValid():
            return
        row = index.row()
        self._model.move_down(row)
        self._table.setCurrentIndex(self._model.index(row + 1, 0))

    # -- Public API (matches original PlotListWindow interface) --

    def _next_color(self) -> str:
        color = COLORS[self._color_index % len(COLORS)]
        self._color_index += 1
        return color

    def add_signal(self, arb_id: int, signal_name: str, unit: str = ""):
        color = self._next_color()
        width = 2
        added = self._model.add_entry(arb_id, signal_name, unit, color, width)
        if added:
            self.signal_added.emit(arb_id, signal_name, unit, color, width)

    def remove_signal(self, arb_id: int, signal_name: str):
        for i, entry in enumerate(self._model.entries):
            if entry.arb_id == arb_id and entry.signal_name == signal_name:
                self._model.remove_entry(i)
                self.signal_removed.emit(arb_id, signal_name)
                return

    def get_signal_settings(self, arb_id: int, signal_name: str) -> dict | None:
        entry = self._model.get_entry(arb_id, signal_name)
        if entry is None:
            return None
        return {
            "color": entry.color,
            "width": entry.width,
            "visible": entry.visible,
        }
