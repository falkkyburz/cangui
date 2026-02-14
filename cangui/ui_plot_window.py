from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QToolBar, QHBoxLayout,
    QLabel, QDoubleSpinBox, QCheckBox,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut

import pyqtgraph as pg

from cangui.service_plot_data import PlotDataService


class PlotWindow(QWidget):
    """Signal plotting window with pyqtgraph — full-width plot."""

    TITLE = "Plot"

    record_toggled = Signal(bool)  # True = start recording, False = stop

    def __init__(self, plot_service: PlotDataService, parent=None):
        super().__init__(parent)
        self._plot_service = plot_service
        self._curves: dict[tuple[int, str], pg.PlotDataItem] = {}
        self._auto_range = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        clear_action = QAction("Clear Data", self)
        clear_action.triggered.connect(self._on_clear_data)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()

        # Time span control
        toolbar.addWidget(QLabel(" Time span (s): "))
        self._time_span_spin = QDoubleSpinBox()
        self._time_span_spin.setRange(1.0, 3600.0)
        self._time_span_spin.setValue(plot_service.time_window)
        self._time_span_spin.setDecimals(1)
        self._time_span_spin.setSingleStep(1.0)
        self._time_span_spin.valueChanged.connect(self._on_time_span_changed)
        toolbar.addWidget(self._time_span_spin)

        toolbar.addSeparator()

        # Auto-range checkbox
        self._auto_range_cb = QCheckBox("Auto Range")
        self._auto_range_cb.setChecked(True)
        self._auto_range_cb.toggled.connect(self._on_auto_range_toggled)
        toolbar.addWidget(self._auto_range_cb)

        toolbar.addSeparator()

        # Record plot trace button
        self._record_action = QAction("Record Plot Trace", self)
        self._record_action.setCheckable(True)
        self._record_action.toggled.connect(self.record_toggled)
        toolbar.addAction(self._record_action)

        layout.addWidget(toolbar)

        # Plot widget — fills full area
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("w")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setLabel("bottom", "Time", "s")
        self._plot_widget.addLegend()
        self._plot_widget.setMouseEnabled(x=True, y=True)
        layout.addWidget(self._plot_widget)

        # Keyboard shortcuts for pan/zoom
        self._setup_shortcuts()

        # Update timer
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._update_plot)
        self._update_timer.start()

    def _setup_shortcuts(self):
        def _shortcut(key, slot):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(slot)

        _shortcut(Qt.Key.Key_Plus, lambda: self._zoom(0.8))
        _shortcut(Qt.Key.Key_Minus, lambda: self._zoom(1.25))
        _shortcut(Qt.Key.Key_Home, self._reset_range)

    def _zoom(self, factor: float):
        self._auto_range_cb.setChecked(False)
        vb = self._plot_widget.getViewBox()
        vb.scaleBy((factor, factor))

    def _reset_range(self):
        self._auto_range_cb.setChecked(True)
        self._plot_widget.enableAutoRange()

    @property
    def primary_view(self):
        return self._plot_widget

    def set_update_interval(self, ms: int):
        self._update_timer.setInterval(ms)

    def add_signal_curve(self, arb_id: int, signal_name: str, unit: str,
                         color: str, width: int):
        """Add a curve for a signal. Called from MainWindow when PlotListWindow adds a signal."""
        key = (arb_id, signal_name)
        if key in self._curves:
            return
        self._plot_service.add_signal(arb_id, signal_name, unit)
        label = f"{signal_name}"
        if unit:
            label += f" ({unit})"
        curve = self._plot_widget.plot(
            pen=pg.mkPen(color, width=width),
            name=label,
        )
        self._curves[key] = curve

    def remove_signal_curve(self, arb_id: int, signal_name: str):
        """Remove a signal curve."""
        key = (arb_id, signal_name)
        curve = self._curves.pop(key, None)
        if curve is not None:
            self._plot_widget.removeItem(curve)
            self._plot_service.remove_signal(arb_id, signal_name)

    def update_curve_style(self, arb_id: int, signal_name: str, settings: dict):
        """Update curve color, width, visibility from PlotListWindow."""
        key = (arb_id, signal_name)
        curve = self._curves.get(key)
        if curve is None:
            return
        color = settings.get("color", "#1f77b4")
        width = settings.get("width", 2)
        visible = settings.get("visible", True)
        curve.setPen(pg.mkPen(color, width=width))
        curve.setVisible(visible)

    def clear_all_curves(self):
        """Remove all curves."""
        self._plot_service.clear()
        for curve in self._curves.values():
            self._plot_widget.removeItem(curve)
        self._curves.clear()

    def _on_clear_data(self):
        """Clear plot data but keep curves."""
        self._plot_service.clear()

    def _on_time_span_changed(self, value: float):
        self._plot_service.time_window = value

    def _on_auto_range_toggled(self, checked: bool):
        self._auto_range = checked
        if checked:
            self._plot_widget.enableAutoRange()
        else:
            self._plot_widget.disableAutoRange()

    def _update_plot(self):
        for key, curve in self._curves.items():
            data = self._plot_service.get_display_data(key)
            if data is None:
                continue
            curve.setData(data[0], data[1])
        if self._auto_range:
            self._plot_widget.enableAutoRange()
