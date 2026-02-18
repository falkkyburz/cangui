"""Mixin and view subclasses that make Tab/Shift+Tab move between
editable columns instead of jumping to the next row."""

from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeView
from PySide6.QtCore import Qt, QModelIndex, QRect
from PySide6.QtGui import QPainter, QColor, QPen

from pyqtgraph.parametertree import ParameterTree

_SEL_FOCUS   = QColor("#2B7CD3")
_SEL_NOFOCUS = QColor("#D0DDF2")
_IND_ON_BLUE = QColor("#FFFFFF")
_IND_ON_LIGHT= QColor("#3A5A8A")
_IND_NORMAL  = QColor("#666668")


class _BranchIndicatorMixin:
    """Draws a small enclosed [+]/[−] indicator that stays visible on any
    selection background.  Replaces the CSS branch rules which suppress the
    default Qt indicator when any QTreeView::branch property is set."""

    def drawBranches(self, painter: QPainter, rect: QRect,
                     index: QModelIndex) -> None:
        sel = self.selectionModel()
        selected = sel is not None and sel.isSelected(index)

        # Selection background for the branch gutter
        if selected:
            painter.fillRect(
                rect,
                _SEL_NOFOCUS if self.property("focused") == "false"
                else _SEL_FOCUS,
            )

        # Indicator only for items that can be expanded
        if self.model() is None or self.model().rowCount(index) == 0:
            return

        is_expanded = self.isExpanded(index)

        sz  = 11
        x   = rect.right() - sz - 2
        y   = rect.top() + (rect.height() - sz) // 2
        box = QRect(x, y, sz, sz)

        if selected:
            color = (
                _IND_ON_LIGHT
                if self.property("focused") == "false"
                else _IND_ON_BLUE
            )
        else:
            color = _IND_NORMAL

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)

        cx, cy = box.center().x(), box.center().y()
        arm = 3
        painter.drawLine(cx - arm, cy, cx + arm, cy)   # horizontal bar
        if not is_expanded:
            painter.drawLine(cx, cy - arm, cx, cy + arm)   # vertical bar → [+]

        painter.restore()


class _TabEditMixin:
    """Mixin that overrides moveCursor so Tab advances through editable
    columns left-to-right, wrapping to the next row when exhausted."""

    def moveCursor(self, action, modifiers):
        if action == QAbstractItemView.CursorAction.MoveNext:
            return self._next_editable(self.currentIndex(), forward=True)
        if action == QAbstractItemView.CursorAction.MovePrevious:
            return self._next_editable(self.currentIndex(), forward=False)
        return super().moveCursor(action, modifiers)

    def _next_editable(self, current: QModelIndex, forward: bool) -> QModelIndex:
        if not current.isValid():
            return current
        model = self.model()
        parent = current.parent()
        row = current.row()
        col = current.column()
        n_cols = model.columnCount(parent)
        step = 1 if forward else -1

        # Search remaining columns in the current row
        col += step
        while 0 <= col < n_cols:
            idx = model.index(row, col, parent)
            if idx.flags() & Qt.ItemFlag.ItemIsEditable:
                return idx
            col += step

        # Wrap to the next/previous row, starting from the first/last column
        row += step
        if 0 <= row < model.rowCount(parent):
            scan = range(n_cols) if forward else range(n_cols - 1, -1, -1)
            for c in scan:
                idx = model.index(row, c, parent)
                if idx.flags() & Qt.ItemFlag.ItemIsEditable:
                    return idx

        return current


class TabTreeView(_BranchIndicatorMixin, _TabEditMixin, QTreeView):
    pass


class TabTableView(_TabEditMixin, QTableView):
    pass


class TabParameterTree(_BranchIndicatorMixin, _TabEditMixin, ParameterTree):
    pass
