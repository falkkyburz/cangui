import json

from PySide6.QtWidgets import QSplitter, QTabWidget


class WorkspaceService:
    """Saves and restores splitter sizes and tab state."""

    def __init__(
        self,
        h_splitter: QSplitter,
        v_splitter: QSplitter,
        rxtx_splitter: QSplitter,
        main_tabs: QTabWidget,
        small_tabs: QTabWidget,
        list_tabs: QTabWidget,
    ):
        self._h_splitter = h_splitter
        self._v_splitter = v_splitter
        self._rxtx_splitter = rxtx_splitter
        self._main_tabs = main_tabs
        self._small_tabs = small_tabs
        self._list_tabs = list_tabs

    def save_state(self) -> str:
        """Serialize layout state to a JSON string."""
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
        """Restore layout state from a JSON string."""
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
        order = [tw.tabText(i) for i in range(tw.count())]
        return {"order": order, "current": tw.currentIndex()}

    @staticmethod
    def _restore_tab_state(tw: QTabWidget, state: dict):
        order = state.get("order", [])
        # Build map from tab text -> widget
        tabs = {tw.tabText(i): (tw.widget(i), tw.tabText(i)) for i in range(tw.count())}
        # Reorder tabs
        for target_idx, title in enumerate(order):
            if title in tabs:
                for i in range(tw.count()):
                    if tw.tabText(i) == title:
                        if i != target_idx:
                            tw.tabBar().moveTab(i, target_idx)
                        break
        current = state.get("current", 0)
        if 0 <= current < tw.count():
            tw.setCurrentIndex(current)
