"""Serialise and restore the 3-pane workspace layout.

The workspace state is a JSON object stored inside the project file under the
``workspace_state`` key (as a JSON-encoded string).  It captures splitter
pixel sizes and the tab order / active index for each of the three
``QTabWidget`` panes.
"""

import json

from PySide6.QtWidgets import QSplitter, QTabWidget


class WorkspaceService:
    """Serialises and restores splitter sizes and QTabWidget state.

    Holds references to the three splitters and three tab widgets that make up
    the cangui 3-pane layout.  Call :meth:`save_state` just before writing the
    project file and :meth:`restore_state` right after loading one.

    Args:
        h_splitter: Horizontal splitter separating the main pane from the right
            column.
        v_splitter: Vertical splitter within the right column, separating the
            small pane (top) from the list pane (bottom).
        rxtx_splitter: Internal splitter inside the Receive/Transmit window
            (RX tree, TX tree, connection table).
        main_tabs: ``QTabWidget`` hosting the main pane tabs (RX/TX, Trace,
            Plot, Diagnostics, Database).
        small_tabs: ``QTabWidget`` hosting the top-right pane tabs (Project
            Manager, Log).
        list_tabs: ``QTabWidget`` hosting the bottom-right pane tabs (Watch,
            Watch DID, DTC, Rx Filter, Plot List, Settings, Help).
    """

    def __init__(
        self,
        h_splitter: QSplitter,
        v_splitter: QSplitter,
        rxtx_splitter: QSplitter,
        main_tabs: QTabWidget,
        small_tabs: QTabWidget,
        list_tabs: QTabWidget,
    ):
        """Bind the service to the six layout widgets it manages.

        Args:
            h_splitter: Horizontal splitter separating the main and side panes.
            v_splitter: Vertical splitter within the side pane.
            rxtx_splitter: Splitter inside the RX/TX panel (RX tree / TX tree).
            main_tabs: QTabWidget for the main (left) pane.
            small_tabs: QTabWidget for the small top-right pane.
            list_tabs: QTabWidget for the bottom-right list pane.
        """
        self._h_splitter = h_splitter
        self._v_splitter = v_splitter
        self._rxtx_splitter = rxtx_splitter
        self._main_tabs = main_tabs
        self._small_tabs = small_tabs
        self._list_tabs = list_tabs

    def save_state(self) -> str:
        """Capture the current layout as a JSON string.

        Returns:
            JSON-encoded string with the keys ``h_splitter``,
            ``v_splitter``, ``rxtx_splitter``, ``main_tabs``,
            ``small_tabs``, and ``list_tabs``.  Splitter values are pixel
            size lists; tab values include ``order`` (list of tab label
            strings) and ``current`` (active index integer).
        """
        state = {
            "h_splitter": self._h_splitter.sizes(),
            "v_splitter": self._v_splitter.sizes(),
            "rxtx_splitter": self._rxtx_splitter.sizes(),
            "main_tabs": self._tab_state(self._main_tabs),
            "small_tabs": self._tab_state(self._small_tabs),
            "list_tabs": self._tab_state(self._list_tabs),
        }
        return json.dumps(state)

    def restore_state(self, state_str: str) -> bool:
        """Apply a previously saved layout string to the live widgets.

        Missing keys are silently ignored, so partial state strings (e.g., from
        older project files that pre-date a new splitter) load gracefully.

        Args:
            state_str: JSON string produced by :meth:`save_state`.

        Returns:
            ``True`` if the string was parsed successfully; ``False`` if it
            was empty or contained invalid JSON.
        """
        if not state_str:
            return False
        try:
            state = json.loads(state_str)
        except (json.JSONDecodeError, TypeError):
            return False

        if "h_splitter" in state:
            self._h_splitter.setSizes(state["h_splitter"])
        if "v_splitter" in state:
            self._v_splitter.setSizes(state["v_splitter"])
        if "rxtx_splitter" in state:
            self._rxtx_splitter.setSizes(state["rxtx_splitter"])
        if "main_tabs" in state:
            self._restore_tab_state(self._main_tabs, state["main_tabs"])
        if "small_tabs" in state:
            self._restore_tab_state(self._small_tabs, state["small_tabs"])
        if "list_tabs" in state:
            self._restore_tab_state(self._list_tabs, state["list_tabs"])
        return True

    @staticmethod
    def _tab_state(tw: QTabWidget) -> dict:
        """Capture tab order and active index for one QTabWidget.

        Args:
            tw: The tab widget to snapshot.

        Returns:
            ``{"order": [label, …], "current": int}``
        """
        order = [tw.tabText(i) for i in range(tw.count())]
        return {"order": order, "current": tw.currentIndex()}

    @staticmethod
    def _restore_tab_state(tw: QTabWidget, state: dict):
        """Restore tab order and active index for one QTabWidget.

        Reorders tabs by moving them on the tab bar without recreating
        widgets or destroying their models.  Unknown tab labels in *state*
        are silently ignored.

        Args:
            tw: The tab widget to restore.
            state: Dict with ``"order"`` (list of label strings) and
                ``"current"`` (active index integer).
        """
        order = state.get("order", [])
        # Reorder tabs by moving on the tab bar
        for target_idx, title in enumerate(order):
            for i in range(tw.count()):
                if tw.tabText(i) == title:
                    if i != target_idx:
                        tw.tabBar().moveTab(i, target_idx)
                    break
        current = state.get("current", 0)
        if 0 <= current < tw.count():
            tw.setCurrentIndex(current)
