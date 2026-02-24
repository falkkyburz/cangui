"""Replay control panel for CTB trace playback.

Provides toolbar controls for configuring and executing CTB trace replay with
mode selection, speed, bus override, loop mode, and playback controls
(pause/resume/reset).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QToolBar, QLabel, QDoubleSpinBox, QComboBox, QSpinBox,
    QToolButton, QFileDialog, QVBoxLayout, QGridLayout, QGroupBox,
)
from PySide6.QtGui import QAction

from cangui.icons import icon as _icon
from cangui.theme import SECONDARY_TEXT_STYLE
from cangui.ui_base_dock_window import BaseDockWindow
from cangui.worker_ctb_player import ReplayMode


class ReplayWindow(BaseDockWindow):
    """Replay control panel for CTB trace playback.

    Provides a toolbar-driven interface for configuring CTB trace replay with
    mode selection, speed control, bus override, loop mode, and playback
    controls (pause/resume/reset). Also includes load functionality for
    selecting trace files.

    Signals:
        load_trace_requested: Emitted with the chosen file path when the user
            selects a trace file to load.
        replay_requested: Emitted when starting replay with current settings.
        stop_requested: Emitted when stopping replay.
        pause_requested: Emitted when pausing replay.
        resume_requested: Emitted when resuming replay.
        reset_requested: Emitted when resetting and restarting replay.
    """

    load_trace_requested = Signal(str)  # file path
    replay_requested = Signal()
    stop_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    reset_requested = Signal()

    TITLE = "Replay"

    def __init__(self, parent=None):
        """Initialize the ReplayWindow with replay controls.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)

        # Main toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        # Load button
        load_action = QAction(_icon("open"), "Load...", self)
        load_action.triggered.connect(self._on_load)
        toolbar.addAction(load_action)

        toolbar.addSeparator()

        # Speed selector for replay
        toolbar.addWidget(QLabel(" Speed: "))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 100.0)
        self._speed_spin.setSingleStep(0.5)
        self._speed_spin.setDecimals(1)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setSuffix("x")
        self._speed_spin.setFixedWidth(72)
        toolbar.addWidget(self._speed_spin)

        self._max_speed_btn = QToolButton()
        self._max_speed_btn.setText("Max")
        self._max_speed_btn.setCheckable(True)
        self._max_speed_btn.toggled.connect(
            lambda on: self._speed_spin.setEnabled(not on)
        )
        toolbar.addWidget(self._max_speed_btn)

        toolbar.addSeparator()

        # Replay mode selector
        toolbar.addWidget(QLabel(" Mode: "))
        self._replay_mode_combo = QComboBox()
        self._replay_mode_combo.addItems(["Tx Only", "Rx Inject", "All"])
        self._replay_mode_combo.setFixedWidth(100)
        toolbar.addWidget(self._replay_mode_combo)

        # Bus selector
        toolbar.addWidget(QLabel(" Bus: "))
        self._replay_bus_spin = QSpinBox()
        self._replay_bus_spin.setRange(0, 16)
        self._replay_bus_spin.setValue(0)
        self._replay_bus_spin.setToolTip("Target bus (0 = use original)")
        self._replay_bus_spin.setFixedWidth(50)
        toolbar.addWidget(self._replay_bus_spin)

        # Loop button
        self._loop_btn = QToolButton()
        self._loop_btn.setText("↺ Loop")
        self._loop_btn.setCheckable(True)
        toolbar.addWidget(self._loop_btn)

        # Progress label
        self._progress_label = QLabel("0.0 / 0.0 s")
        self._progress_label.setStyleSheet("color: gray;")
        self._progress_label.setFixedWidth(120)
        toolbar.addWidget(self._progress_label)

        toolbar.addSeparator()

        # Start button
        self._start_action = QAction(_icon("play"), "Start [F9]", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        # Stop button
        self._stop_action = QAction(_icon("stop"), "Stop [F6]", self)
        self._stop_action.setEnabled(False)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()

        # Playback control buttons
        self._pause_btn = QToolButton()
        self._pause_btn.setIcon(_icon("pause"))
        self._pause_btn.setToolTip("Pause replay [P]")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause)
        toolbar.addWidget(self._pause_btn)

        self._resume_btn = QToolButton()
        self._resume_btn.setIcon(_icon("play"))
        self._resume_btn.setToolTip("Resume replay [R]")
        self._resume_btn.setEnabled(False)
        self._resume_btn.clicked.connect(self._on_resume)
        toolbar.addWidget(self._resume_btn)

        self._reset_btn = QToolButton()
        self._reset_btn.setIcon(_icon("refresh"))
        self._reset_btn.setToolTip("Reset and restart replay")
        self._reset_btn.setEnabled(False)
        self._reset_btn.clicked.connect(self._on_reset)
        toolbar.addWidget(self._reset_btn)

        toolbar.addSeparator()

        self._file_label = QLabel("")
        self._file_label.setStyleSheet(SECONDARY_TEXT_STYLE)
        toolbar.addWidget(self._file_label)

        self._layout.addWidget(toolbar)

        # Info panel showing loaded file and trace statistics
        info_group = QGroupBox("Loaded Trace Information")
        info_layout = QGridLayout()
        info_layout.setSpacing(8)

        # File info
        info_layout.addWidget(QLabel("File:"), 0, 0)
        self._info_file = QLabel("(no file loaded)")
        self._info_file.setStyleSheet("color: gray;")
        info_layout.addWidget(self._info_file, 0, 1)

        # Frame count
        info_layout.addWidget(QLabel("Frames:"), 1, 0)
        self._info_frames = QLabel("0")
        info_layout.addWidget(self._info_frames, 1, 1)

        # Duration
        info_layout.addWidget(QLabel("Duration:"), 1, 2)
        self._info_duration = QLabel("0.0 s")
        info_layout.addWidget(self._info_duration, 1, 3)

        # Status
        info_layout.addWidget(QLabel("Status:"), 2, 0)
        self._info_status = QLabel("Idle")
        self._info_status.setStyleSheet("color: gray;")
        info_layout.addWidget(self._info_status, 2, 1)

        # Buses
        info_layout.addWidget(QLabel("Buses:"), 2, 2)
        self._info_buses = QLabel("—")
        info_layout.addWidget(self._info_buses, 2, 3)

        info_layout.setColumnStretch(1, 1)
        info_layout.setColumnStretch(3, 1)
        info_group.setLayout(info_layout)
        self._layout.addWidget(info_group)

        self._layout.addStretch()

    @property
    def speed_factor(self) -> float:
        """Return the numeric replay speed multiplier from the toolbar.

        Returns ``1000.0`` when the Max button is checked (unlimited speed),
        otherwise returns the spinbox value.

        Returns:
            Floating-point speed multiplier (e.g. ``0.5``, ``1.0``, ``1000.0``).
        """
        if self._max_speed_btn.isChecked():
            return 1000.0
        return self._speed_spin.value()

    @property
    def replay_mode(self) -> str:
        """Return the current replay mode as a string.

        Returns:
            One of "tx_only", "rx_only", or "all".
        """
        mode_map = {0: "tx_only", 1: "rx_only", 2: "all"}
        return mode_map.get(self._replay_mode_combo.currentIndex(), "all")

    @property
    def replay_bus(self) -> int:
        """Return the target bus number for Tx frames.

        Returns:
            0-16, where 0 means use original bus from trace.
        """
        return self._replay_bus_spin.value()

    @property
    def replay_loop(self) -> bool:
        """Return whether loop mode is enabled.

        Returns:
            True if the loop button is checked.
        """
        return self._loop_btn.isChecked()

    def set_replay_progress(self, current: float, total: float):
        """Update the progress label with current and total time.

        Args:
            current: Current playback time in seconds.
            total: Total trace duration in seconds.
        """
        self._progress_label.setText(f"{current:.1f} / {total:.1f} s")

    def set_replay_state(self, playing: bool):
        """Update button states based on replay state.

        Args:
            playing: ``True`` while a trace file is being replayed, ``False``
                when playback has stopped.
        """
        if playing:
            # Disable config during playback
            self._replay_mode_combo.setEnabled(False)
            self._replay_bus_spin.setEnabled(False)
            self._loop_btn.setEnabled(False)
            self._speed_spin.setEnabled(not self._max_speed_btn.isChecked())
            # Enable start/stop
            self._start_action.setEnabled(False)
            self._stop_action.setEnabled(True)
            # Enable pause/resume/reset during playback
            self._pause_btn.setEnabled(True)
            self._resume_btn.setEnabled(False)
            self._reset_btn.setEnabled(True)
            # Update status
            self.set_replay_status("Playing", "#006699")
        else:
            # Enable config when stopped
            self._replay_mode_combo.setEnabled(True)
            self._replay_bus_spin.setEnabled(True)
            self._loop_btn.setEnabled(True)
            self._speed_spin.setEnabled(True)
            # Enable start/stop
            self._start_action.setEnabled(True)
            self._stop_action.setEnabled(False)
            # Disable pause/resume/reset when stopped
            self._pause_btn.setEnabled(False)
            self._resume_btn.setEnabled(False)
            self._reset_btn.setEnabled(False)
            # Update status
            self.set_replay_status("Idle", "gray")

    def set_file_label(self, path: str):
        """Update the file label to show currently loaded trace file.

        Args:
            path: Absolute path to the trace file, or empty string to clear.
        """
        if path:
            from pathlib import Path
            self._file_label.setText(Path(path).name)
            self._file_label.setToolTip(path)
            self._info_file.setText(Path(path).name)
            self._info_file.setToolTip(path)
        else:
            self._file_label.setText("")
            self._file_label.setToolTip("")
            self._info_file.setText("(no file loaded)")
            self._info_file.setStyleSheet("color: gray;")

    def update_trace_info(self, frame_count: int, duration: float, buses: set):
        """Update trace statistics display.

        Args:
            frame_count: Total number of frames in the trace.
            duration: Total duration in seconds.
            buses: Set of unique bus numbers in the trace.
        """
        self._info_frames.setText(str(frame_count))
        self._info_duration.setText(f"{duration:.1f} s")
        if buses:
            bus_str = ", ".join(str(b) for b in sorted(buses))
            self._info_buses.setText(bus_str)
        else:
            self._info_buses.setText("—")

    def set_replay_status(self, status: str, color: str = "gray"):
        """Update the replay status display.

        Args:
            status: Status text (e.g., "Idle", "Playing", "Paused").
            color: CSS color for the status text.
        """
        self._info_status.setText(status)
        self._info_status.setStyleSheet(f"color: {color};")

    def _on_load(self):
        """Open a file-open dialog and emit load_trace_requested with the path.

        Supports loading .ctb, .trc, and .blf files. For .trc and .blf files,
        automatically converts to .ctb format before loading.

        Emits:
            load_trace_requested: With the chosen file path, if the user
                confirmed the dialog.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Trace", "",
            "All Trace Files (*.ctb *.trc *.blf);;CTB Files (*.ctb);;TRC Files (*.trc);;BLF Files (*.blf);;All Files (*)"
        )
        if path:
            self.load_trace_requested.emit(path)

    def _on_start(self):
        """Emit replay_requested to start trace replay.

        Emits:
            replay_requested: When the Start button is clicked.
        """
        self.replay_requested.emit()

    def _on_stop(self):
        """Emit stop_requested to stop active replay.

        Emits:
            stop_requested: When the Stop button is clicked.
        """
        self.stop_requested.emit()

    def _on_pause(self):
        """Emit pause_requested to pause active replay.

        Emits:
            pause_requested: When the Pause button is clicked.
        """
        self.pause_requested.emit()
        self._pause_btn.setEnabled(False)
        self._resume_btn.setEnabled(True)
        self.set_replay_status("Paused", "#CC9900")

    def _on_resume(self):
        """Emit resume_requested to resume paused replay.

        Emits:
            resume_requested: When the Resume button is clicked.
        """
        self.resume_requested.emit()
        self._pause_btn.setEnabled(True)
        self._resume_btn.setEnabled(False)
        self.set_replay_status("Playing", "#006699")

    def _on_reset(self):
        """Emit reset_requested to reset and restart replay.

        Emits:
            reset_requested: When the Reset button is clicked.
        """
        self.reset_requested.emit()
        self._pause_btn.setEnabled(True)
        self._resume_btn.setEnabled(False)
        self.set_replay_status("Playing", "#006699")
