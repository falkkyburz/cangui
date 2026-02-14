from PySide6.QtWidgets import (
    QHeaderView, QTableView, QToolBar, QComboBox, QStyledItemDelegate,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt

from cangui.model_rx_filter import RxFilterModel, FilterAction
from cangui.ui_base_dock_window import BaseDockWindow


class ActionDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Action column (Pass/Drop)."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems([a.value for a in FilterAction])
        return combo

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class RxFilterWindow(BaseDockWindow):
    TITLE = "Rx Filter"

    def __init__(self, model: RxFilterModel, parent=None):
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        add_pass_action = QAction("Add Pass", self)
        add_pass_action.triggered.connect(lambda: self._model.add_rule(FilterAction.PASS))
        toolbar.addAction(add_pass_action)

        add_drop_action = QAction("Add Drop", self)
        add_drop_action.triggered.connect(lambda: self._model.add_rule(FilterAction.DROP))
        toolbar.addAction(add_drop_action)

        remove_action = QAction("Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        toolbar.addSeparator()

        up_action = QAction("Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction("Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        self._layout.addWidget(toolbar)

        self._view = QTableView()
        self._view.setModel(self._model)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._view.setItemDelegateForColumn(1, ActionDelegate(self._view))
        self._layout.addWidget(self._view)

    @property
    def primary_view(self):
        return self._view

    def _on_remove(self):
        index = self._view.currentIndex()
        if index.isValid():
            self._model.remove_rule(index.row())

    def _on_move_up(self):
        index = self._view.currentIndex()
        if index.isValid():
            self._model.move_up(index.row())
            new_row = max(0, index.row() - 1)
            self._view.setCurrentIndex(self._model.index(new_row, index.column()))

    def _on_move_down(self):
        index = self._view.currentIndex()
        if index.isValid():
            self._model.move_down(index.row())
            new_row = min(self._model.rowCount() - 1, index.row() + 1)
            self._view.setCurrentIndex(self._model.index(new_row, index.column()))
