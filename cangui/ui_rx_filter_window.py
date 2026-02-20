"""RX Filter dock window for cangui.

Allows the user to define pass/drop rules that gate incoming CAN frames
before they reach the RX model and trace.  Backed by
:class:`~cangui.model_rx_filter.RxFilterModel`.
"""
from PySide6.QtWidgets import (
    QHeaderView, QToolBar, QComboBox, QStyledItemDelegate,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QTimer

from cangui.model_rx_filter import RxFilterModel, FilterAction
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.ui_tab_navigation import TabTableView
from cangui.icons import icon as _icon


class ActionDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Action column (Pass/Drop)."""

    def createEditor(self, parent, option, index):
        """Return a QComboBox pre-populated with Pass/Drop choices."""
        combo = QComboBox(parent)
        combo.addItems([a.value for a in FilterAction])
        return combo

    def setEditorData(self, editor, index):
        """Select the current action value in the combo-box editor."""
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Write the selected action string back to the model."""
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class RxFilterWindow(BaseDockWindow):
    """RX filter rule editor panel.

    Presents :class:`~cangui.model_rx_filter.RxFilterModel` in a table with
    columns for CAN ID (hex), mask (hex), and action (Pass/Drop).  The user
    can add pass or drop rules, remove rules, and reorder them — rules are
    evaluated top-to-bottom by the dispatcher.
    """

    TITLE = "Rx Filter"

    def __init__(self, model: RxFilterModel, parent=None):
        """Build the filter panel with its toolbar and table.

        Args:
            model: The :class:`~cangui.model_rx_filter.RxFilterModel` to display.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._model = model

        toolbar = QToolBar()
        toolbar.setMovable(False)

        add_pass_action = QAction(_icon("pass"), "Add Pass", self)
        add_pass_action.triggered.connect(lambda: self._model.add_rule(FilterAction.PASS))
        toolbar.addAction(add_pass_action)

        add_drop_action = QAction(_icon("drop"), "Add Drop", self)
        add_drop_action.triggered.connect(lambda: self._model.add_rule(FilterAction.DROP))
        toolbar.addAction(add_drop_action)

        remove_action = QAction(_icon("remove"), "Remove", self)
        remove_action.triggered.connect(self._on_remove)
        toolbar.addAction(remove_action)

        toolbar.addSeparator()

        up_action = QAction(_icon("up"), "Up", self)
        up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(up_action)

        down_action = QAction(_icon("down"), "Down", self)
        down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(down_action)

        self._layout.addWidget(toolbar)

        self._view = TabTableView()
        self._view.setModel(self._model)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(TabTableView.SelectionBehavior.SelectRows)
        self._view.setSelectionMode(TabTableView.SelectionMode.ExtendedSelection)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._view.setItemDelegateForColumn(1, ActionDelegate(self._view))
        self._layout.addWidget(self._view)

        self._model.rowsInserted.connect(lambda *_: self._resize_columns())
        self._model.modelReset.connect(lambda *_: self._resize_columns())
        QTimer.singleShot(0, self._resize_columns)

    def _resize_columns(self):
        """Resize all columns except the last to fit their contents."""
        for i in range(self._model.columnCount() - 1):
            self._view.resizeColumnToContents(i)

    @property
    def primary_view(self):
        """The table view that receives keyboard focus."""
        return self._view

    def _on_remove(self):
        """Remove all selected filter rules from the model."""
        rows = sorted(
            {i.row() for i in self._view.selectionModel().selectedIndexes()
             if i.column() == 0},
            reverse=True,
        )
        if not rows:
            idx = self._view.currentIndex()
            if idx.isValid():
                rows = [idx.row()]
        for row in rows:
            self._model.remove_rule(row)

    def _on_move_up(self):
        """Move the selected filter rule one row up and follow with the selection."""
        index = self._view.currentIndex()
        if index.isValid():
            self._model.move_up(index.row())
            new_row = max(0, index.row() - 1)
            self._view.setCurrentIndex(self._model.index(new_row, index.column()))

    def _on_move_down(self):
        """Move the selected filter rule one row down and follow with the selection."""
        index = self._view.currentIndex()
        if index.isValid():
            self._model.move_down(index.row())
            new_row = min(self._model.rowCount() - 1, index.row() + 1)
            self._view.setCurrentIndex(self._model.index(new_row, index.column()))
