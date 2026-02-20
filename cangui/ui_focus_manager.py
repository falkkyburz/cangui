"""Application-level keyboard focus management for dock window routing.

This module provides FocusManager, which intercepts bare key-press events and
routes them to the appropriate dock panel, enabling single-key panel switching
and coordinated selection clearing across panes.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QComboBox, QAbstractSpinBox, QTextEdit,
    QTabWidget, QTreeView, QWidget,
)


class FocusManager(QObject):
    """Application-level event filter for single-key window switching.

    Maintains a registry of dock windows paired with their parent tab widget
    and an activation key. When a bare digit key is pressed (no modifiers) and
    focus is not inside an editable widget, the corresponding panel is brought
    to the front and given keyboard focus.

    Additionally coordinates selection clearing: activating a selection in one
    registered view automatically clears selections in all other views.

    Attributes:
        _entries: Ordered list of ``(key_text, window, tab_widget, label)``
            tuples for every registered dock window.
        _key_map: Mapping from Qt key code to index in ``_entries``.
        _selecting: Reentrancy guard for the cross-view selection handler.
    """

    def __init__(self, parent=None):
        """Initialise an empty FocusManager with no registered windows.

        Args:
            parent: Optional QObject parent.
        """
        super().__init__(parent)
        # (key_text, window, tab_widget, label)
        self._entries: list[tuple[str, QWidget, QTabWidget, str]] = []
        self._key_map: dict[int, int] = {}  # Qt.Key -> entry index
        self._selecting = False

    def register(self, key: str, window: QWidget, tab_widget: QTabWidget, label: str):
        """Register a dock window so it can be activated by a key press.

        Connects the ``selectionChanged`` signal of every selectable view
        inside ``window`` to the cross-pane selection-clearing handler.

        Args:
            key: Single character used as the activation shortcut (e.g. ``"1"``).
            window: The dock widget that should receive focus.
            tab_widget: The QTabWidget that contains ``window`` as a page.
            label: Human-readable name used for display purposes.
        """
        index = len(self._entries)
        self._entries.append((key, window, tab_widget, label))
        # Map digit key text to Qt key code
        qt_key = getattr(Qt.Key, f"Key_{key}", None)
        if qt_key is not None:
            self._key_map[qt_key] = index
        for view in self._get_selectable_views(window):
            sm = view.selectionModel()
            if sm is not None:
                sm.selectionChanged.connect(
                    lambda sel, _, w=window: self._on_view_selected(sel, w)
                )

    def _get_selectable_views(self, window):
        """Return all item views inside ``window`` that participate in selection sync.

        Checks for a ``selectable_views`` attribute first; falls back to the
        single ``primary_view`` attribute if present.

        Args:
            window: The dock widget to inspect.

        Returns:
            A list of views (possibly empty) that expose a ``selectionModel``.
        """
        if hasattr(window, 'selectable_views'):
            return window.selectable_views
        pv = getattr(window, 'primary_view', None)
        if pv is not None and hasattr(pv, 'selectionModel'):
            return [pv]
        return []

    def _on_view_selected(self, selected, source_window):
        """Clear selections in all panes other than the one that just gained a selection.

        Protected by the ``_selecting`` reentrancy guard to avoid recursive
        signal loops when clearing other views.

        Args:
            selected: The ``QItemSelection`` that was just applied.
            source_window: The window whose view triggered this callback.
        """
        if self._selecting or not selected.indexes():
            return
        self._selecting = True
        try:
            for _key, window, _tab, _label in self._entries:
                if window is source_window:
                    continue
                for view in self._get_selectable_views(window):
                    view.clearSelection()
        finally:
            self._selecting = False

    def install(self):
        """Install this object as an application-wide event filter."""
        QApplication.instance().installEventFilter(self)

    def activate(self, index: int):
        """Bring the registered window at ``index`` to the front and focus it.

        Raises the containing tab page, moves keyboard focus to the window's
        ``primary_view``, and refreshes the ``focused`` CSS property on all
        registered views so the stylesheet can highlight the active pane.

        Args:
            index: Zero-based index into the internal entries list.
                   Out-of-range values are silently ignored.
        """
        if index < 0 or index >= len(self._entries):
            return
        _key, window, tab_widget, _label = self._entries[index]
        tab_widget.setCurrentWidget(window)
        view = getattr(window, "primary_view", None)
        if view is not None:
            view.setFocus()
        self._update_focus_properties(window)

    def _update_focus_properties(self, active_window: QWidget):
        """Refresh the ``focused`` Qt property on every registered primary view.

        Sets the property to ``"true"`` for the active window and ``"false"``
        for all others, then re-polishes each view so the stylesheet picks up
        the change immediately.

        Args:
            active_window: The window that is now considered focused.
        """
        for _key, window, _tab, _label in self._entries:
            view = getattr(window, "primary_view", None)
            if view is None:
                continue
            value = "true" if window is active_window else "false"
            view.setProperty("focused", value)
            view.style().unpolish(view)
            view.style().polish(view)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Intercept bare digit and F1 key presses to switch dock panels.

        Ignores events that occur while a modifier key is held or when focus
        is inside an editable widget (so the user can still type digits).
        Routes Space to expand/collapse tree rows.

        Args:
            obj: The QObject that received the event (unused).
            event: The event to inspect.

        Returns:
            ``True`` if the event was consumed, ``False`` otherwise.
        """
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
