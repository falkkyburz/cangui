from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QComboBox, QAbstractSpinBox, QTextEdit,
    QTabWidget, QTreeView, QWidget,
)


class FocusManager(QObject):
    """Application-level event filter for single-key window switching."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # (key_text, window, tab_widget, label)
        self._entries: list[tuple[str, QWidget, QTabWidget, str]] = []
        self._key_map: dict[int, int] = {}  # Qt.Key -> entry index

    def register(self, key: str, window: QWidget, tab_widget: QTabWidget, label: str):
        index = len(self._entries)
        self._entries.append((key, window, tab_widget, label))
        # Map digit key text to Qt key code
        qt_key = getattr(Qt.Key, f"Key_{key}", None)
        if qt_key is not None:
            self._key_map[qt_key] = index

    def install(self):
        QApplication.instance().installEventFilter(self)

    def activate(self, index: int):
        if index < 0 or index >= len(self._entries):
            return
        _key, window, tab_widget, _label = self._entries[index]
        tab_widget.setCurrentWidget(window)
        view = getattr(window, "primary_view", None)
        if view is not None:
            view.setFocus()
        self._update_focus_properties(window)

    def _update_focus_properties(self, active_window: QWidget):
        for _key, window, _tab, _label in self._entries:
            view = getattr(window, "primary_view", None)
            if view is None:
                continue
            value = "true" if window is active_window else "false"
            view.setProperty("focused", value)
            view.style().unpolish(view)
            view.style().polish(view)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False

        # Only intercept bare keypresses (no Ctrl, Alt, etc.)
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False

        # Skip if focus is in an editable widget
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QComboBox, QAbstractSpinBox)):
            return False
        if isinstance(focus, QTextEdit) and not focus.isReadOnly():
            return False

        key = event.key()

        # Space → toggle expand/collapse in tree views
        if key == Qt.Key.Key_Space and isinstance(focus, QTreeView):
            idx = focus.currentIndex()
            if idx.isValid():
                idx = idx.sibling(idx.row(), 0)
                if focus.model().hasChildren(idx):
                    focus.setExpanded(idx, not focus.isExpanded(idx))
                    return True

        # F1 → Help (last entry)
        if key == Qt.Key.Key_F1:
            self.activate(len(self._entries) - 1)
            return True

        index = self._key_map.get(key)
        if index is not None:
            self.activate(index)
            return True

        return False
