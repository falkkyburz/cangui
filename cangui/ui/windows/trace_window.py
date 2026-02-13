from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolBar, QTableView,
    QFileDialog, QComboBox, QLabel, QLineEdit,
)
from PySide6.QtGui import QAction

from cangui.models.trace_model import TraceModel


class TraceWindow(QWidget):
    """Trace recording window with Start/Pause/Stop/Save/Load controls."""

    TITLE = "Trace"

    load_trace_requested = Signal(str)  # file path
    save_trace_requested = Signal(str)  # file path

    def __init__(self, model: TraceModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._auto_scroll = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        self._start_action = QAction("Start [F9]", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._pause_action = QAction("Pause", self)
        self._pause_action.setEnabled(False)
        self._pause_action.triggered.connect(self._on_pause)
        toolbar.addAction(self._pause_action)

        self._stop_action = QAction("Stop [F6]", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        self._clear_action = QAction("Clear", self)
        self._clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(self._clear_action)

        toolbar.addSeparator()

        save_action = QAction("Save...", self)
        save_action.triggered.connect(self._on_save)
        toolbar.addAction(save_action)

        load_action = QAction("Load...", self)
        load_action.triggered.connect(self._on_load)
        toolbar.addAction(load_action)

        toolbar.addSeparator()

        # Speed selector for replay
        toolbar.addWidget(QLabel(" Speed: "))
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["0.5x", "1x", "2x", "10x", "Max"])
        self._speed_combo.setCurrentIndex(1)
        toolbar.addWidget(self._speed_combo)

        layout.addWidget(toolbar)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(4, 2, 4, 2)
        filter_layout.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("CAN ID or text...")
        self._filter_edit.setClearButtonEnabled(True)
        filter_layout.addWidget(self._filter_edit)
        layout.addLayout(filter_layout)

        # Table view
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        # Use fixed widths instead of ResizeToContents (which measures ALL rows)
        for col, width in enumerate([60, 80, 35, 70, 35, 45, 35, 200]):
            header.resizeSection(col, width)
        layout.addWidget(self._table)

        # Auto-scroll on new rows
        self._model.entries_committed.connect(self._on_entries_committed)
        self._model.modelReset.connect(self._on_model_reset)
        self._model.file_changed.connect(self._on_file_changed)

        # File label
        self._file_label = QLabel("")
        self._file_label.setStyleSheet("color: gray; padding: 0 4px;")
        layout.addWidget(self._file_label)

        # Status bar
        self._status_layout = QHBoxLayout()
        self._status_layout.setContentsMargins(4, 2, 4, 2)
        self._count_label = QLabel("Messages: 0")
        self._status_layout.addWidget(self._count_label)
        self._status_layout.addStretch()
        self._state_label = QLabel("Stopped")
        self._status_layout.addWidget(self._state_label)
        layout.addLayout(self._status_layout)

    @property
    def primary_view(self):
        return self._table

    @property
    def speed_factor(self) -> float:
        text = self._speed_combo.currentText()
        if text == "Max":
            return 1000.0
        return float(text.rstrip("x"))

    def _update_button_state(self, recording: bool):
        self._start_action.setEnabled(not recording)
        self._pause_action.setEnabled(recording)
        self._stop_action.setEnabled(recording)

    def _on_start(self):
        self._model.start()
        self._update_button_state(True)
        self._state_label.setText("Recording")

    def _on_pause(self):
        self._model.pause()
        self._update_button_state(False)
        self._start_action.setEnabled(True)
        self._state_label.setText("Paused")

    def _on_stop(self):
        self._model.stop()
        self._update_button_state(False)
        self._state_label.setText("Stopped")

    def _on_clear(self):
        self._model.clear()
        self._count_label.setText("Messages: 0")

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Trace", "", "Trace Files (*.trc);;All Files (*)"
        )
        if path:
            self.save_trace_requested.emit(path)

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Trace", "", "Trace Files (*.trc);;All Files (*)"
        )
        if path:
            self.load_trace_requested.emit(path)

    def _on_entries_committed(self):
        self._count_label.setText(f"Messages: {self._model.message_count}")
        if self._auto_scroll:
            self._table.scrollToBottom()

    def _on_model_reset(self):
        self._count_label.setText(f"Messages: {self._model.message_count}")

    def _on_file_changed(self, path: str):
        if path:
            self._file_label.setText(path)
        else:
            self._file_label.setText("")

    def set_replay_state(self, playing: bool):
        """Update UI to reflect replay state."""
        if playing:
            self._state_label.setText("Replaying")
        else:
            self._state_label.setText("Stopped")
