from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
)

from cangui.core.database_manager import DatabaseManager


class SignalSelector(QWidget):
    """Tree widget for browsing and selecting signals from loaded DBC files.

    Shows a Message → Signal hierarchy with search filtering.
    """

    signal_selected = Signal(int, str, str)  # arb_id, signal_name, unit

    def __init__(self, db_manager: DatabaseManager, parent=None):
        super().__init__(parent)
        self._db = db_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter signals...")
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Unit"])
        self._tree.setColumnWidth(0, 200)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree)

    def refresh(self):
        self._tree.clear()
        for msg in self._db.dbc.messages:
            msg_item = QTreeWidgetItem([f"0x{msg.frame_id:03X} - {msg.name}", ""])
            msg_item.setData(0, 0x100, msg.frame_id)  # store arb_id
            for sig in msg.signals:
                sig_item = QTreeWidgetItem([sig.name, sig.unit or ""])
                sig_item.setData(0, 0x100, msg.frame_id)
                sig_item.setData(0, 0x101, sig.name)
                sig_item.setData(0, 0x102, sig.unit or "")
                msg_item.addChild(sig_item)
            self._tree.addTopLevelItem(msg_item)

    def _apply_filter(self, text: str):
        text = text.lower()
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_visible = False
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                visible = not text or text in sig_item.text(0).lower()
                sig_item.setHidden(not visible)
                if visible:
                    msg_visible = True
            # Show message if it matches or any child matches
            if not text or text in msg_item.text(0).lower():
                msg_visible = True
            msg_item.setHidden(not msg_visible)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        sig_name = item.data(0, 0x101)
        if sig_name is None:
            return  # Clicked a message node, not a signal
        arb_id = item.data(0, 0x100)
        unit = item.data(0, 0x102) or ""
        self.signal_selected.emit(arb_id, sig_name, unit)
