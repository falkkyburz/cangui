"""Base class module for all dockable panel windows in the cangui application.

Every panel hosted inside a QTabWidget pane should inherit from
``BaseDockWindow``.  The base class provides the shared zero-margin vertical
layout (``self._layout``) and the protocol for tab-label registration and
focus-manager integration.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout


class BaseDockWindow(QWidget):
    """Base class for every panel that lives inside a QTabWidget pane.

    All dockable windows **must** override the ``TITLE`` class attribute with a
    human-readable name used for tab labels and focus-manager registration.
    Windows that contain a primary interactive view (tree or table) **should**
    override the ``primary_view`` property so the focus manager can direct
    keyboard input to the right widget.

    The constructor creates ``self._layout`` (a zero-margin ``QVBoxLayout``)
    which subclasses use directly instead of creating their own top-level
    layout.
    """

    #: Human-readable panel title.  Subclasses must override.
    TITLE: str = "Untitled"

    def __init__(self, parent=None):
        """Initialize the base dock window and create the shared layout.

        Args:
            parent: Optional parent QWidget.  Passed through to QWidget.
        """
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    @property
    def primary_view(self):
        """Return the main interactive widget (tree/table) for focus routing.

        Subclasses that have a primary tree or table view should override this
        property and return that widget.  The default implementation returns
        ``None``, which tells the focus manager to focus the window itself.

        Returns:
            The primary interactive widget, or ``None`` if not overridden.
        """
        return None
