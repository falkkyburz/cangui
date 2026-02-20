from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QLineEdit, QPushButton, QTextEdit,
    QGroupBox, QSpinBox, QLabel, QToolBar, QSplitter, QFileDialog,
)
# QVBoxLayout retained for sub-widget layouts inside this file
from PySide6.QtGui import QAction

from cangui.uds_client import UdsResponse
from cangui.security_loader import SecurityLoader
from cangui.service_uds import UdsService
from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow


# Common UDS sessions
SESSIONS = {
    "Default (0x01)": 0x01,
    "Programming (0x02)": 0x02,
    "Extended (0x03)": 0x03,
}

# Common UDS services for raw mode
SERVICE_TEMPLATES = {
    "DiagnosticSessionControl (0x10)": "10 01",
    "ECUReset (0x11)": "11 01",
    "ReadDID (0x22)": "22 F1 90",
    "WriteDID (0x2E)": "2E F1 90",
    "SecurityAccess Seed (0x27)": "27 01",
    "TesterPresent (0x3E)": "3E 00",
    "ReadDTCInfo (0x19)": "19 02 FF",
    "ClearDTC (0x14)": "14 FF FF FF",
    "RoutineControl (0x31)": "31 01 FF 00",
}


class DiagnosticWindow(BaseDockWindow):
    """Diagnostic window for UDS communication."""

    TITLE = "Diagnostics"

    connect_requested = Signal(int, int, int)  # tx_id, rx_id, bus_number
    disconnect_requested = Signal()

    def __init__(self, uds_service: UdsService, parent=None):
        """Build the Diagnostics panel with connection settings, UDS controls, and a log.

        Args:
            uds_service: The :class:`~cangui.service_uds.UdsService` that
                executes all UDS requests.
            parent: Optional Qt parent widget.
        """
        super().__init__(parent)
        self._uds = uds_service
        self._security_loader = SecurityLoader()
        self._layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._connect_action = QAction(_icon("connect"), "Connect", self)
        self._connect_action.triggered.connect(self._on_connect)
        toolbar.addAction(self._connect_action)

        self._disconnect_action = QAction(_icon("disconnect"), "Disconnect", self)
        self._disconnect_action.setEnabled(False)
        self._disconnect_action.triggered.connect(self._on_disconnect)
        toolbar.addAction(self._disconnect_action)

        toolbar.addSeparator()

        self._tester_present_action = QAction(_icon("heartbeat"), "Tester Present", self)
        self._tester_present_action.triggered.connect(self._uds.tester_present)
        toolbar.addAction(self._tester_present_action)

        self._layout.addWidget(toolbar)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)

        # Top: controls
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        # Connection settings
        conn_group = QGroupBox("Connection")
        conn_form = QFormLayout(conn_group)

        self._bus_spin = QSpinBox()
        self._bus_spin.setRange(1, 99)
        self._bus_spin.setValue(1)
        self._bus_spin.setMaximumWidth(100)
        conn_form.addRow("Bus:", self._bus_spin)

        self._tx_id_edit = QLineEdit("7E0")
        self._tx_id_edit.setMaximumWidth(100)
        conn_form.addRow("TX ID (hex):", self._tx_id_edit)

        self._rx_id_edit = QLineEdit("7E8")
        self._rx_id_edit.setMaximumWidth(100)
        conn_form.addRow("RX ID (hex):", self._rx_id_edit)

        controls_layout.addWidget(conn_group)

        # Session control
        session_group = QGroupBox("Session Control (0x10)")
        session_layout = QHBoxLayout(session_group)

        self._session_combo = QComboBox()
        for name in SESSIONS:
            self._session_combo.addItem(name)
        session_layout.addWidget(self._session_combo)

        session_btn = QPushButton("Change Session")
        session_btn.clicked.connect(self._on_change_session)
        session_layout.addWidget(session_btn)

        controls_layout.addWidget(session_group)

        # Read DID
        read_group = QGroupBox("Read DID (0x22)")
        read_layout = QHBoxLayout(read_group)

        read_layout.addWidget(QLabel("DID (hex):"))
        self._read_did_edit = QLineEdit("F190")
        self._read_did_edit.setMaximumWidth(100)
        read_layout.addWidget(self._read_did_edit)

        read_btn = QPushButton("Read")
        read_btn.clicked.connect(self._on_read_did)
        read_layout.addWidget(read_btn)

        read_layout.addStretch()
        controls_layout.addWidget(read_group)

        # Write DID
        write_group = QGroupBox("Write DID (0x2E)")
        write_layout = QHBoxLayout(write_group)

        write_layout.addWidget(QLabel("DID (hex):"))
        self._write_did_edit = QLineEdit("F190")
        self._write_did_edit.setMaximumWidth(100)
        write_layout.addWidget(self._write_did_edit)

        write_layout.addWidget(QLabel("Data (hex):"))
        self._write_data_edit = QLineEdit()
        self._write_data_edit.setPlaceholderText("AA BB CC ...")
        write_layout.addWidget(self._write_data_edit)

        write_btn = QPushButton("Write")
        write_btn.clicked.connect(self._on_write_did)
        write_layout.addWidget(write_btn)

        controls_layout.addWidget(write_group)

        # Raw request
        raw_group = QGroupBox("Raw Request")
        raw_layout = QHBoxLayout(raw_group)

        self._raw_template_combo = QComboBox()
        self._raw_template_combo.addItem("Custom")
        for name in SERVICE_TEMPLATES:
            self._raw_template_combo.addItem(name)
        self._raw_template_combo.currentTextChanged.connect(self._on_template_changed)
        raw_layout.addWidget(self._raw_template_combo)

        self._raw_data_edit = QLineEdit()
        self._raw_data_edit.setPlaceholderText("10 01")
        raw_layout.addWidget(self._raw_data_edit)

        raw_btn = QPushButton("Send")
        raw_btn.clicked.connect(self._on_raw_request)
        raw_layout.addWidget(raw_btn)

        controls_layout.addWidget(raw_group)

        # Security Access
        sec_group = QGroupBox("Security Access (0x27)")
        sec_layout = QVBoxLayout(sec_group)

        sec_top = QHBoxLayout()
        sec_top.addWidget(QLabel("Level:"))
        self._sec_level_spin = QSpinBox()
        self._sec_level_spin.setRange(1, 127)
        self._sec_level_spin.setValue(1)
        self._sec_level_spin.setPrefix("0x")
        self._sec_level_spin.setDisplayIntegerBase(16)
        self._sec_level_spin.setMaximumWidth(80)
        sec_top.addWidget(self._sec_level_spin)
        sec_top.addStretch()

        sec_unlock_btn = QPushButton("Unlock")
        sec_unlock_btn.clicked.connect(self._on_security_unlock)
        sec_top.addWidget(sec_unlock_btn)
        sec_layout.addLayout(sec_top)

        sec_file_layout = QHBoxLayout()
        sec_file_layout.addWidget(QLabel("Key file:"))
        self._sec_file_edit = QLineEdit()
        self._sec_file_edit.setPlaceholderText("Path to seed-key .py file...")
        self._sec_file_edit.setReadOnly(True)
        sec_file_layout.addWidget(self._sec_file_edit)

        sec_browse_btn = QPushButton("Browse...")
        sec_browse_btn.clicked.connect(self._on_browse_security_file)
        sec_file_layout.addWidget(sec_browse_btn)
        sec_layout.addLayout(sec_file_layout)

        controls_layout.addWidget(sec_group)
        controls_layout.addStretch()

        splitter.addWidget(controls)

        # Bottom: response log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFontFamily("monospace")
        splitter.addWidget(self._log)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        self._layout.addWidget(splitter)

        # Wire UDS service signals
        self._uds.response_received.connect(self._on_response)
        self._uds.error_occurred.connect(self._on_error)
        self._uds.connection_changed.connect(self._on_connection_changed)

    @property
    def primary_view(self):
        """The log text area that receives keyboard focus."""
        return self._log

    def _get_tx_id(self) -> int:
        """Parse the TX-ID field as a hex integer, defaulting to 0x7E0 on error."""
        try:
            return int(self._tx_id_edit.text(), 16)
        except ValueError:
            return 0x7E0

    def _get_rx_id(self) -> int:
        """Parse the RX-ID field as a hex integer, defaulting to 0x7E8 on error."""
        try:
            return int(self._rx_id_edit.text(), 16)
        except ValueError:
            return 0x7E8

    def _on_connect(self):
        """Emit :attr:`connect_requested` with the configured TX ID, RX ID, and bus number."""
        self.connect_requested.emit(self._get_tx_id(), self._get_rx_id(), self._bus_spin.value())

    def _on_disconnect(self):
        """Emit :attr:`disconnect_requested` to close the active UDS session."""
        self.disconnect_requested.emit()

    def _on_connection_changed(self, connected: bool):
        """Update toolbar actions and log a status message when the UDS connection changes.

        Args:
            connected: ``True`` when a connection was just established.
        """
        self._connect_action.setEnabled(not connected)
        self._disconnect_action.setEnabled(connected)
        if connected:
            self._log_message("Connected", "UDS connection opened")
        else:
            self._log_message("Disconnected", "UDS connection closed")

    def _on_change_session(self):
        """Read the session combo and issue a DiagnosticSessionControl request."""
        name = self._session_combo.currentText()
        session = SESSIONS.get(name, 0x01)
        self._log_message("Request", f"DiagnosticSessionControl → session 0x{session:02X}")
        self._uds.change_session(session)

    def _on_read_did(self):
        """Parse the DID field and issue a ReadDataByIdentifier (0x22) request."""
        try:
            did = int(self._read_did_edit.text(), 16)
        except ValueError:
            self._log_message("Error", "Invalid DID value")
            return
        self._log_message("Request", f"ReadDID → 0x{did:04X}")
        self._uds.read_did(did)

    def _on_write_did(self):
        """Parse the DID and data fields and issue a WriteDataByIdentifier (0x2E) request."""
        try:
            did = int(self._write_did_edit.text(), 16)
        except ValueError:
            self._log_message("Error", "Invalid DID value")
            return
        try:
            data = bytes.fromhex(self._write_data_edit.text().replace(" ", ""))
        except ValueError:
            self._log_message("Error", "Invalid data hex")
            return
        self._log_message("Request", f"WriteDID → 0x{did:04X} data={data.hex(' ').upper()}")
        self._uds.write_did(did, data)

    def _on_template_changed(self, text: str):
        """Fill the raw data field with the byte pattern for the selected service template.

        Args:
            text: Name of the selected template from the combo box.
        """
        template = SERVICE_TEMPLATES.get(text)
        if template:
            self._raw_data_edit.setText(template)

    def _on_raw_request(self):
        """Parse the raw hex field and issue it as an unframed UDS request."""
        try:
            data = bytes.fromhex(self._raw_data_edit.text().replace(" ", ""))
        except ValueError:
            self._log_message("Error", "Invalid hex data")
            return
        self._log_message("Request", f"Raw → {data.hex(' ').upper()}")
        self._uds.raw_request(data)

    def _on_browse_security_file(self):
        """Open a file dialog to select a seed-key plugin and load it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Seed-Key File", "",
            "Python Files (*.py);;All Files (*)"
        )
        if not path:
            return
        try:
            self._security_loader.load(path)
            self._sec_file_edit.setText(path)
            self._log_message("Security", f"Loaded key algorithm: {path}")
        except Exception as e:
            self._log_message("Error", f"Failed to load security file: {e}")

    def _on_security_unlock(self):
        """Issue a SecurityAccess (0x27) request using the configured level and key algorithm."""
        level = self._sec_level_spin.value()
        if not self._security_loader.is_loaded:
            self._log_message("Request", f"SecurityAccess → level 0x{level:02X} (seed only, no key file)")
            self._uds.security_access(level, None)
        else:
            self._log_message("Request", f"SecurityAccess → level 0x{level:02X} (with key algorithm)")
            self._uds.security_access(level, self._security_loader.calculate_key)

    def _on_response(self, resp: UdsResponse):
        """Append a formatted response or error entry to the log text area."""
        if resp.success:
            msg = f"[+] {resp.service_name}"
            if resp.did:
                msg += f" DID=0x{resp.did:04X}"
            if resp.data:
                msg += f" → {resp.data_hex}"
                # Try ASCII interpretation
                printable = "".join(
                    chr(b) if 32 <= b < 127 else "." for b in resp.data
                )
                msg += f"  ({printable})"
            self._log_message("Response", msg)
        else:
            msg = f"[-] {resp.service_name}"
            if resp.did:
                msg += f" DID=0x{resp.did:04X}"
            msg += f": {resp.error}"
            self._log_message("Response", msg)

    def _on_error(self, error: str):
        """Append a UDS error message to the log.

        Args:
            error: Human-readable error string from UdsWorker.
        """
        self._log_message("Error", error)

    def _log_message(self, tag: str, message: str):
        """Append a timestamped ``[tag] message`` line to the log area.

        Args:
            tag: Short category label (e.g. ``"Request"``, ``"Response"``, ``"Error"``).
            message: The body text to log.
        """
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.append(f"[{ts}] [{tag}] {message}")
