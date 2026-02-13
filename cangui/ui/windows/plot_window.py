from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSplitter, QToolBar
from PySide6.QtGui import QAction

import pyqtgraph as pg

from cangui.core.database_manager import DatabaseManager
from cangui.services.plot_data_service import PlotDataService
from cangui.ui.widgets.signal_selector import SignalSelector

# Predefined colors for plot curves
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


class PlotWindow(QWidget):
    """Signal plotting window with pyqtgraph and signal selector sidebar."""

    TITLE = "Plot"

    def __init__(
        self,
        plot_service: PlotDataService,
        db_manager: DatabaseManager,
        parent=None,
    ):
        super().__init__(parent)
        self._plot_service = plot_service
        self._db_manager = db_manager
        self._curves: dict[tuple[int, str], pg.PlotDataItem] = {}
        self._color_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        clear_action = QAction("Clear", self)
        clear_action.triggered.connect(self._on_clear)
        toolbar.addAction(clear_action)

        remove_action = QAction("Remove Selected", self)
        remove_action.triggered.connect(self._on_remove_selected)
        toolbar.addAction(remove_action)

        layout.addWidget(toolbar)

        # Splitter: plot on left, signal selector on right
        splitter = QSplitter()

        # Plot widget
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("w")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setLabel("bottom", "Time", "s")
        self._plot_widget.addLegend()
        splitter.addWidget(self._plot_widget)

        # Signal selector
        self._signal_selector = SignalSelector(db_manager)
        self._signal_selector.signal_selected.connect(self._add_signal)
        splitter.addWidget(self._signal_selector)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

        # Update timer
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(50)
        self._update_timer.timeout.connect(self._update_plot)
        self._update_timer.start()

    @property
    def primary_view(self):
        return self._plot_widget

    def refresh_signals(self):
        self._signal_selector.refresh()

    def _next_color(self) -> str:
        color = COLORS[self._color_index % len(COLORS)]
        self._color_index += 1
        return color

    def _add_signal(self, arb_id: int, signal_name: str, unit: str):
        key = (arb_id, signal_name)
        if key in self._curves:
            return  # Already plotted
        self._plot_service.add_signal(arb_id, signal_name, unit)
        color = self._next_color()
        label = f"{signal_name}"
        if unit:
            label += f" ({unit})"
        curve = self._plot_widget.plot(
            pen=pg.mkPen(color, width=2),
            name=label,
        )
        self._curves[key] = curve

    def _on_clear(self):
        self._plot_service.clear()
        for curve in self._curves.values():
            self._plot_widget.removeItem(curve)
        self._curves.clear()
        self._color_index = 0

    def _on_remove_selected(self):
        # Remove last added signal as a simple removal approach
        if not self._curves:
            return
        key = list(self._curves.keys())[-1]
        curve = self._curves.pop(key)
        self._plot_widget.removeItem(curve)
        self._plot_service.remove_signal(*key)

    def _update_plot(self):
        for key, curve in self._curves.items():
            buf = self._plot_service.buffers.get(key)
            if buf is None or len(buf.times) == 0:
                continue
            curve.setData(buf.times, buf.values)
