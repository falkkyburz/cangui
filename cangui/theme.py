LIGHT_THEME = """
/* ── Global Base ──────────────────────────────────────────── */
QWidget {
    background-color: #FAFAFA;
    color: #1C1C1E;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #F0F1F3;
}

/* ── Splitter ─────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #C4C5C9;
}
QSplitter::handle:horizontal {
    width: 4px;
}
QSplitter::handle:vertical {
    height: 4px;
}
QSplitter::handle:hover {
    background-color: #2B7CD3;
}

/* ── Tab Widget ───────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #D0D1D5;
    background-color: #FFFFFF;
    top: -1px;
}
QTabBar::tab {
    background-color: #E2E3E7;
    color: #5A5A5E;
    border: 1px solid #C8C9CC;
    border-bottom: none;
    padding: 5px 14px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    min-width: 60px;
}
QTabBar::tab:selected {
    background-color: #FFFFFF;
    color: #1C1C1E;
    border-bottom-color: #FFFFFF;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background-color: #ECECEF;
    color: #1C1C1E;
}

/* ── Toolbar ──────────────────────────────────────────────── */
QToolBar {
    background-color: #EDEEF2;
    border: none;
    border-bottom: 1px solid #D0D1D5;
    spacing: 2px;
    padding: 2px 4px;
}
QToolBar::separator {
    background-color: #C4C5C9;
    width: 1px;
    margin: 3px 4px;
}
QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 3px 6px;
    color: #1C1C1E;
}
QToolButton:hover {
    background-color: #DCDDE1;
    border-color: #BDBEC2;
}
QToolButton:pressed {
    background-color: #CACBCF;
}
QToolButton:disabled {
    color: #AEAFB3;
}
QToolBar QLabel {
    background-color: transparent;
    padding: 0 2px;
}

/* ── Tree and Table Views ─────────────────────────────────── */
QTreeView, QTableView {
    background-color: #FFFFFF;
    alternate-background-color: #F4F6FA;
    border: 1px solid #D0D1D5;
    gridline-color: #E4E5EA;
    outline: none;
}
QTreeView::item, QTableView::item {
    padding: 2px 4px;
    border: none;
}
QTreeView::item:hover, QTableView::item:hover {
    background-color: #E8F0FD;
}
QTreeView::item:selected, QTableView::item:selected {
    background-color: #2B7CD3;
    color: #FFFFFF;
}
QTreeView[focused="false"]::item:selected,
QTableView[focused="false"]::item:selected {
    background-color: #D0DDF2;
    color: #404040;
}


/* ── Header ───────────────────────────────────────────────── */
QHeaderView {
    background-color: #EDEEF2;
}
QHeaderView::section {
    background-color: #EDEEF2;
    color: #3A3A3E;
    border: none;
    border-right: 1px solid #D0D1D5;
    border-bottom: 1px solid #D0D1D5;
    padding: 4px 6px;
    font-weight: bold;
    font-size: 12px;
}
QHeaderView::section:last {
    border-right: none;
}

/* ── Scrollbars ───────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: #F0F1F3;
    width: 10px;
    border: none;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #BABBBE;
    min-height: 24px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #9A9B9F;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #F0F1F3;
    height: 10px;
    border: none;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #BABBBE;
    min-width: 24px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #9A9B9F;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── Input Widgets ────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #C4C5C9;
    border-radius: 3px;
    padding: 3px 5px;
    selection-background-color: #2B7CD3;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #2B7CD3;
}
QSpinBox, QDoubleSpinBox {
    background-color: #FFFFFF;
    border: 1px solid #C4C5C9;
    border-radius: 3px;
    padding: 3px 5px;
    selection-background-color: #2B7CD3;
    selection-color: #FFFFFF;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2B7CD3;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    border: none;
    background-color: transparent;
    width: 14px;
}
QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #C4C5C9;
    border-radius: 3px;
    padding: 3px 5px;
    selection-background-color: #2B7CD3;
    selection-color: #FFFFFF;
}
QComboBox:focus {
    border-color: #2B7CD3;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #C4C5C9;
    selection-background-color: #2B7CD3;
    selection-color: #FFFFFF;
    outline: none;
}

/* ── Push Button ──────────────────────────────────────────── */
QPushButton {
    background-color: #E8E9ED;
    border: 1px solid #C4C5C9;
    border-radius: 4px;
    padding: 4px 12px;
    color: #1C1C1E;
    min-height: 22px;
}
QPushButton:hover {
    background-color: #DCDDE1;
    border-color: #AEAFB3;
}
QPushButton:pressed {
    background-color: #CACBCF;
}
QPushButton:disabled {
    color: #AEAFB3;
    border-color: #DCDDE1;
    background-color: #F0F1F3;
}
QPushButton:default {
    background-color: #2B7CD3;
    border-color: #1A5FA8;
    color: #FFFFFF;
}
QPushButton:default:hover {
    background-color: #3D8EE0;
}

/* ── Checkbox ─────────────────────────────────────────────── */
QCheckBox {
    spacing: 5px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #C4C5C9;
    border-radius: 3px;
    background-color: #FFFFFF;
}
QCheckBox::indicator:checked {
    background-color: #2B7CD3;
    border-color: #2B7CD3;
}
QCheckBox::indicator:hover {
    border-color: #2B7CD3;
}

/* ── GroupBox ─────────────────────────────────────────────── */
QGroupBox {
    font-weight: bold;
    border: 1px solid #D0D1D5;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: #3A3A3E;
    background-color: transparent;
}

/* ── Menu ─────────────────────────────────────────────────── */
QMenu {
    background-color: #FFFFFF;
    border: 1px solid #C8C9CC;
    border-radius: 4px;
    padding: 4px 0;
}
QMenu::item {
    padding: 5px 24px 5px 16px;
    color: #1C1C1E;
}
QMenu::item:selected {
    background-color: #2B7CD3;
    color: #FFFFFF;
}
QMenu::separator {
    height: 1px;
    background-color: #E4E5EA;
    margin: 4px 8px;
}

/* ── Status Bar ───────────────────────────────────────────── */
QStatusBar {
    background-color: #EDEEF2;
    border-top: 1px solid #D0D1D5;
    color: #5A5A5E;
    font-size: 12px;
}

/* ── Inline editors inside tree / table views ────────────── */
QTreeView QLineEdit, QTableView QLineEdit {
    padding: 0px 3px;
    border-radius: 0;
    min-height: 0;
}
QTreeView QSpinBox, QTableView QSpinBox,
QTreeView QDoubleSpinBox, QTableView QDoubleSpinBox {
    padding: 0px 3px;
    border-radius: 0;
    min-height: 0;
}
QTreeView QComboBox, QTableView QComboBox {
    padding: 0px 3px;
    border-radius: 0;
    min-height: 0;
}

/* ── Tooltip ──────────────────────────────────────────────── */
QToolTip {
    background-color: #FFFDE7;
    border: 1px solid #C8C9CC;
    color: #1C1C1E;
    padding: 3px 6px;
    border-radius: 3px;
}
"""
