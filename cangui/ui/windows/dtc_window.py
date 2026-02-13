from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QTableView, QHeaderView, QComboBox, QLabel,
)
from PySide6.QtGui import QAction, QColor

from cangui.core.dtc_manager import Dtc, DtcManager
from cangui.core.uds_client import UdsResponse
from cangui.services.uds_service import UdsService


DTC_COLUMNS = ["DTC Code", "Display", "Status", "Status Bits", "Details"]

# ReadDTCInformation sub-functions
DTC_SUBFUNCTIONS = {
    "Stored DTCs (0x02)": 0x02,
    "Pending DTCs (0x07)": 0x07,
    "Permanent DTCs (0x15)": 0x15,
}


class DtcModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dtcs: list[Dtc] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._dtcs)

    def columnCount(self, parent=QModelIndex()):
        return len(DTC_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return DTC_COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        dtc = self._dtcs[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            match index.column():
                case 0: return dtc.code_hex
                case 1: return dtc.code_display
                case 2: return dtc.status_text
                case 3: return dtc.status_bits
                case 4:
                    return f"0x{dtc.code:06X} status=0x{dtc.status:02X}"
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if dtc.is_active:
                return QColor(Qt.GlobalColor.red)
            if dtc.is_confirmed:
                return QColor(204, 102, 0)  # Orange
        return None

    def flags(self, index: QModelIndex):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_dtcs(self, dtcs: list[Dtc]):
        self.beginResetModel()
        self._dtcs = dtcs
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self._dtcs.clear()
        self.endResetModel()

    @property
    def dtcs(self) -> list[Dtc]:
        return self._dtcs


class DtcWindow(QWidget):
    """DTC (Diagnostic Trouble Code) display and management window."""

    TITLE = "DTC"

    def __init__(self, uds_service: UdsService, parent=None):
        super().__init__(parent)
        self._uds = uds_service
        self._dtc_manager = DtcManager()
        self._model = DtcModel(self)
        self._pending_read = False
        self._pending_clear = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        toolbar.addWidget(QLabel(" Type: "))
        self._subfunction_combo = QComboBox()
        for name in DTC_SUBFUNCTIONS:
            self._subfunction_combo.addItem(name)
        toolbar.addWidget(self._subfunction_combo)

        toolbar.addSeparator()

        read_action = QAction("Read DTCs", self)
        read_action.triggered.connect(self._on_read)
        toolbar.addAction(read_action)

        clear_action = QAction("Clear DTCs", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        clear_list_action = QAction("Clear List", self)
        clear_list_action.triggered.connect(self._model.clear)
        toolbar.addAction(clear_list_action)

        layout.addWidget(toolbar)

        # Table
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self._table)

        # Status
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        # Wire UDS responses
        self._uds.response_received.connect(self._on_response)

    @property
    def primary_view(self):
        return self._table

    def _on_read(self):
        name = self._subfunction_combo.currentText()
        subfunc = DTC_SUBFUNCTIONS.get(name, 0x02)
        # ReadDTCInformation: 0x19 <sub-function> <status-mask>
        data = bytes([0x19, subfunc, 0xFF])
        self._pending_read = True
        self._status_label.setText("Reading DTCs...")
        self._uds.raw_request(data)

    def _on_clear(self):
        # ClearDiagnosticInformation: 0x14 FF FF FF (all DTCs)
        data = bytes([0x14, 0xFF, 0xFF, 0xFF])
        self._pending_clear = True
        self._status_label.setText("Clearing DTCs...")
        self._uds.raw_request(data)

    def _on_response(self, resp: UdsResponse):
        if resp.service_name != "RawRequest":
            return

        if self._pending_clear:
            self._pending_clear = False
            if resp.success and resp.data and resp.data[0] == 0x54:
                self._status_label.setText("DTCs cleared successfully")
                self._model.clear()
            elif resp.success:
                self._status_label.setText(f"Clear response: {resp.data_hex}")
            else:
                self._status_label.setText(f"Clear failed: {resp.error}")
            return

        if self._pending_read:
            self._pending_read = False
            if resp.success and resp.data and resp.data[0] == 0x59:
                dtcs = self._dtc_manager.parse_report_by_status_mask(resp.data)
                self._model.set_dtcs(dtcs)
                self._status_label.setText(f"Found {len(dtcs)} DTCs")
            elif resp.success:
                self._status_label.setText(f"Unexpected response: {resp.data_hex}")
            else:
                self._status_label.setText(f"Read failed: {resp.error}")
