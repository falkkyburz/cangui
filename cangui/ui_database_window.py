import json
from pathlib import Path

import cantools

from PySide6.QtCore import Qt, Signal, QModelIndex, QTimer, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QWidget, QToolBar, QHeaderView, QMenu,
    QFileDialog, QMessageBox, QStyledItemDelegate, QComboBox,
)
from PySide6.QtGui import QAction

from cangui.model_database import DatabaseModel, COL_BYTE_ORDER
from cangui.ui_tab_navigation import TabTreeView
from cangui.icons import icon as _icon
from cangui.ui_base_dock_window import BaseDockWindow

# Widths for columns 1-15 of the database tree (col 0 = Name, sized dynamically;
# col 16 = Comment, stretches as last section).
_DB_COL_WIDTHS = {
    1:  50,   # Bus
    2:  80,   # ID (hex)
    3:  45,   # DLC
    4:  72,   # Cycle (ms)
    5:  65,   # Start Bit
    6:  72,   # Bit Length
    7: 105,   # Byte Order  ("little_endian" is wide)
    8:  58,   # Signed
    9:  65,   # Factor
    10: 65,   # Offset
    11: 65,   # Min
    12: 65,   # Max
    13: 60,   # Unit
    14: 50,   # Mux
    15: 90,   # Values
}


class _DbSortProxy(QSortFilterProxyModel):
    """Sort proxy for the database tree view.

    Provides numeric sort ordering for integer and hex-integer columns
    (Bus, ID, DLC, Cycle Time, Start Bit, Bit Length).  All three tree
    levels (files, messages, signals) are sorted independently within
    their respective parent.
    """

    def lessThan(self, left, right) -> bool:
        """Compare two source-model cells for sort ordering.

        Args:
            left: Left-hand source model index.
            right: Right-hand source model index.

        Returns:
            ``True`` if *left* should appear before *right* in ascending order.
        """
        col = left.column()
        ld = left.data() or ""
        rd = right.data() or ""

        if col == 1:   # Bus — integer
            try:
                return int(ld) < int(rd)
            except (ValueError, TypeError):
                pass
        elif col == 2:  # ID (hex) — hex integer
            try:
                return int(str(ld), 16) < int(str(rd), 16)
            except (ValueError, TypeError):
                pass
        elif col in (3, 4, 5, 6):  # DLC, Cycle (ms), Start Bit, Bit Length — integer
            try:
                return int(ld) < int(rd)
            except (ValueError, TypeError):
                pass

        return super().lessThan(left, right)


class ByteOrderDelegate(QStyledItemDelegate):
    """Dropdown delegate for the Byte Order column (little_endian / big_endian)."""

    def createEditor(self, parent, option, index):
        """Return a QComboBox pre-populated with the two byte-order choices.

        Args:
            parent: Parent widget for the combo box.
            option: Style option (unused).
            index: Model index of the cell being edited (unused).

        Returns:
            A QComboBox with ``"little_endian"`` and ``"big_endian"`` items.
        """
        combo = QComboBox(parent)
        combo.addItems(["little_endian", "big_endian"])
        return combo

    def setEditorData(self, editor, index):
        """Select the current byte-order value in the combo-box editor.

        Args:
            editor: The QComboBox created by :meth:`createEditor`.
            index: Model index whose edit-role data should be reflected.
        """
        value = index.data(Qt.ItemDataRole.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Write the selected byte-order string back to the model.

        Args:
            editor: The QComboBox whose current text should be committed.
            model: The item model owning the cell.
            index: Model index of the cell to update.
        """
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class DatabaseWindow(BaseDockWindow):
    """Database editor tab — view/edit CAN message and signal definitions."""

    TITLE = "Database"

    database_changed = Signal()
    dbc_imported = Signal(str)  # file path
    add_to_watch_requested = Signal(int, str, str, str)  # arb_id, signal_name, unit, direction
    add_to_plot_requested = Signal(int, str, str)  # arb_id, signal_name, unit
    add_to_tx_requested = Signal(int, int, bool, str, int, int)  # can_id, dlc, is_extended, symbol, cycle_ms, bus

    def __init__(self, parent=None):
        """Build the DatabaseWindow with its toolbar and tree view.

        Constructs all toolbar actions (add database/message/signal, remove,
        move up/down, import/export DBC/JSON, add to Watch/Plot/TX), wires
        model change signals to ``database_changed``, and schedules an initial
        column resize.

        Args:
            parent: Optional parent QWidget.
        """
        super().__init__(parent)

        # Toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)

        add_db = QAction(_icon("database"), "Add Database", self)
        add_db.triggered.connect(self._on_add_database)
        toolbar.addAction(add_db)

        self._add_msg_action = QAction(_icon("message"), "Add Message", self)
        self._add_msg_action.triggered.connect(self._on_add_message)
        toolbar.addAction(self._add_msg_action)

        self._add_sig_action = QAction(_icon("signal"), "Add Signal", self)
        self._add_sig_action.triggered.connect(self._on_add_signal)
        toolbar.addAction(self._add_sig_action)

        self._remove_action = QAction(_icon("trash"), "Remove Selected", self)
        self._remove_action.triggered.connect(self._on_remove_selected)
        toolbar.addAction(self._remove_action)

        toolbar.addSeparator()

        self._up_action = QAction(_icon("up"), "Move Up", self)
        self._up_action.triggered.connect(self._on_move_up)
        toolbar.addAction(self._up_action)

        self._down_action = QAction(_icon("down"), "Move Down", self)
        self._down_action.triggered.connect(self._on_move_down)
        toolbar.addAction(self._down_action)

        toolbar.addSeparator()

        import_dbc = QAction(_icon("import"), "Import DBC", self)
        import_dbc.triggered.connect(self._on_import_dbc)
        toolbar.addAction(import_dbc)

        export_dbc = QAction(_icon("export"), "Export DBC", self)
        export_dbc.triggered.connect(self._on_export_dbc)
        toolbar.addAction(export_dbc)

        toolbar.addSeparator()

        import_json = QAction(_icon("import"), "Import JSON", self)
        import_json.triggered.connect(self._on_import_json)
        toolbar.addAction(import_json)

        export_json = QAction(_icon("export"), "Export JSON", self)
        export_json.triggered.connect(self._on_export_json)
        toolbar.addAction(export_json)

        toolbar.addSeparator()

        self._add_watch_action = QAction(_icon("watch"), "Add to Watch", self)
        self._add_watch_action.triggered.connect(self._on_add_to_watch)
        toolbar.addAction(self._add_watch_action)

        self._add_plot_action = QAction(_icon("plot"), "Add to Plot", self)
        self._add_plot_action.triggered.connect(self._on_add_to_plot)
        toolbar.addAction(self._add_plot_action)

        self._add_tx_action = QAction(_icon("send"), "Add to TX", self)
        self._add_tx_action.setToolTip("Add selected message to the TX list")
        self._add_tx_action.triggered.connect(self._on_add_to_tx)
        toolbar.addAction(self._add_tx_action)

        self._layout.addWidget(toolbar)

        # Model + TreeView
        self._model = DatabaseModel()
        self._model.dataChanged.connect(lambda *_: self.database_changed.emit())
        self._model.rowsInserted.connect(lambda *_: self.database_changed.emit())
        self._model.rowsRemoved.connect(lambda *_: self.database_changed.emit())
        self._model.modelReset.connect(lambda *_: self.database_changed.emit())

        self._db_proxy = _DbSortProxy(self)
        self._db_proxy.setSourceModel(self._model)

        self._tree = TabTreeView()
        self._tree.setModel(self._db_proxy)
        self._tree.setSortingEnabled(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionBehavior(TabTreeView.SelectionBehavior.SelectRows)
        self._tree.setSelectionMode(TabTreeView.SelectionMode.ExtendedSelection)
        self._tree.setItemDelegateForColumn(COL_BYTE_ORDER, ByteOrderDelegate(self._tree))

        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._layout.addWidget(self._tree)
        QTimer.singleShot(0, self._resize_columns)

    def _resize_columns(self):
        """Resize columns to sensible widths after data is loaded."""
        header = self._tree.header()
        # Name column: fit to visible content, minimum 150 px
        self._tree.resizeColumnToContents(0)
        if header.sectionSize(0) < 150:
            header.resizeSection(0, 150)
        # Fixed widths for all data columns
        for col, width in _DB_COL_WIDTHS.items():
            header.resizeSection(col, width)

    @property
    def primary_view(self):
        """The database tree view that receives keyboard focus."""
        return self._tree

    def _on_context_menu(self, pos):
        """Show a context menu for the database tree at the given position."""
        has_sel = self._tree.selectionModel().hasSelection()
        self._remove_action.setEnabled(has_sel)
        self._up_action.setEnabled(has_sel)
        self._down_action.setEnabled(has_sel)
        self._add_watch_action.setEnabled(has_sel)
        self._add_plot_action.setEnabled(has_sel)
        self._add_tx_action.setEnabled(has_sel)
        menu = QMenu(self)
        menu.addAction(self._add_msg_action)
        menu.addAction(self._add_sig_action)
        menu.addAction(self._remove_action)
        menu.addSeparator()
        menu.addAction(self._up_action)
        menu.addAction(self._down_action)
        menu.addSeparator()
        menu.addAction(self._add_watch_action)
        menu.addAction(self._add_plot_action)
        menu.addAction(self._add_tx_action)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # -- Proxy helpers --

    def _map_src(self, proxy_index: QModelIndex) -> QModelIndex:
        """Map a proxy-model index to the underlying source-model index."""
        return self._db_proxy.mapToSource(proxy_index)

    def _map_proxy(self, src_index: QModelIndex) -> QModelIndex:
        """Map a source-model index to the corresponding proxy-model index."""
        return self._db_proxy.mapFromSource(src_index)

    # -- Toolbar actions --

    def _on_add_database(self):
        """Add a new empty database file entry to the model."""
        self._model.add_file()

    def _on_add_message(self):
        """Add a new blank message to the file containing the current selection.

        If nothing is selected, the message is appended to the last file (or a
        new one is created).  If a file node is selected, the tree is expanded
        to show the new child.
        """
        proxy_idx = self._tree.currentIndex()
        if not proxy_idx.isValid():
            # Add to last file, or create one
            self._model.add_message()
            return
        index = self._map_src(proxy_idx)
        level = self._model._get_level(index)
        if level == 0:
            # File selected — add message to this file
            self._model.add_message(index.row())
            self._tree.expand(proxy_idx)
        elif level == 1:
            # Message selected — add message to same file
            file_row, _ = self._model.get_message_row(index)
            self._model.add_message(file_row)
        else:
            # Signal selected — add message to parent file
            file_row, _ = self._model.get_message_row(index)
            self._model.add_message(file_row)

    def _on_add_signal(self):
        """Add a new blank signal to the message containing the current selection.

        Shows an information dialog if no message context can be determined.
        Expands the file and message nodes so the new signal is immediately
        visible.
        """
        proxy_idx = self._tree.currentIndex()
        if not proxy_idx.isValid():
            QMessageBox.information(self, "Add Signal", "Select a message first.")
            return
        index = self._map_src(proxy_idx)
        file_row, msg_row = self._model.get_message_row(index)
        if file_row < 0 or msg_row < 0:
            QMessageBox.information(self, "Add Signal", "Select a message first.")
            return
        self._model.add_signal(file_row, msg_row)
        # Expand the parent so the new signal is visible
        file_idx = self._model.index(file_row, 0)
        msg_idx = self._model.index(msg_row, 0, file_idx)
        self._tree.expand(self._map_proxy(file_idx))
        self._tree.expand(self._map_proxy(msg_idx))

    def _db_selected_indexes(self) -> list:
        """Return source-model column-0 indexes for the current selection.

        Proxy indexes from the tree's selection model are mapped to source
        model indexes so callers can pass them directly to DatabaseModel
        methods without going through the proxy layer.  Falls back to the
        current index when nothing is selected.
        """
        col0 = [self._map_src(i)
                for i in self._tree.selectionModel().selectedIndexes()
                if i.column() == 0]
        if not col0:
            cur = self._map_src(self._tree.currentIndex())
            if cur.isValid():
                col0 = [self._model.index(cur.row(), 0, cur.parent())]
        return col0

    def _db_selected_signals(self) -> list[tuple[int, str, str]]:
        """Return unique (can_id, signal_name, unit) tuples for the selection.

        Signal rows contribute themselves; message rows expand to all signals;
        file rows expand to all signals in all messages.  Results are
        deduplicated by (can_id, signal_name).
        """
        seen: set[tuple[int, str]] = set()
        result: list[tuple[int, str, str]] = []
        for index in self._db_selected_indexes():
            level = self._model._get_level(index)
            if level == 2:
                pair = self._model.get_signal(index)
                if pair:
                    msg, sig = pair
                    key = (msg.can_id, sig.name)
                    if key not in seen:
                        seen.add(key)
                        result.append((msg.can_id, sig.name, sig.unit))
            elif level == 1:
                msg = self._model.get_message(index)
                if msg:
                    for sig in msg.signals:
                        key = (msg.can_id, sig.name)
                        if key not in seen:
                            seen.add(key)
                            result.append((msg.can_id, sig.name, sig.unit))
            elif level == 0:
                file_row = index.row()
                if 0 <= file_row < len(self._model.items):
                    for msg in self._model.items[file_row].messages:
                        for sig in msg.signals:
                            key = (msg.can_id, sig.name)
                            if key not in seen:
                                seen.add(key)
                                result.append((msg.can_id, sig.name, sig.unit))
        return result

    def _db_selected_messages(self) -> list[tuple]:
        """Return unique message tuples (can_id, dlc, is_extended, name, cycle_ms, bus).

        Message rows contribute themselves; signal rows resolve to their parent
        message; file rows expand to all messages.  Deduplication is by can_id.
        """
        seen: set[int] = set()
        result: list[tuple] = []

        def _add_msg(msg, bus: int):
            if msg.can_id not in seen:
                seen.add(msg.can_id)
                result.append((msg.can_id, msg.dlc, msg.can_id > 0x7FF,
                                msg.name, msg.cycle_time_ms, bus))

        for index in self._db_selected_indexes():
            level = self._model._get_level(index)
            if level == 0:
                file_row = index.row()
                if 0 <= file_row < len(self._model.items):
                    file_item = self._model.items[file_row]
                    for msg in file_item.messages:
                        _add_msg(msg, file_item.bus)
            elif level == 1:
                msg = self._model.get_message(index)
                if msg:
                    file_row, _ = self._model.get_message_row(index)
                    bus = (self._model._items[file_row].bus
                           if 0 <= file_row < len(self._model._items) else 1)
                    _add_msg(msg, bus)
            elif level == 2:
                pair = self._model.get_signal(index)
                if pair:
                    msg, _ = pair
                    file_row, _ = self._model.get_message_row(index)
                    bus = (self._model._items[file_row].bus
                           if 0 <= file_row < len(self._model._items) else 1)
                    _add_msg(msg, bus)
        return result

    def _on_remove_selected(self):
        """Remove all selected file, message, or signal nodes.

        When both a parent and one of its children are selected the child is
        skipped — the parent removal takes the children with it.  Items are
        sorted deepest-level-first, highest-row-first within each level and
        removed via QPersistentModelIndex so that earlier removals do not
        invalidate later indices.
        """
        from PySide6.QtCore import QPersistentModelIndex
        indexes = self._db_selected_indexes()
        if not indexes:
            return

        # Build identity set for ancestor-filtering
        selected_set = {(i.internalId(), i.row()) for i in indexes}

        def has_ancestor_selected(idx):
            p = idx.parent()
            while p.isValid():
                if (p.internalId(), p.row()) in selected_set:
                    return True
                p = p.parent()
            return False

        to_remove = [i for i in indexes if not has_ancestor_selected(i)]
        # Deepest level first; within the same level highest row first
        to_remove.sort(key=lambda i: (-self._model._get_level(i), -i.row()))
        persistent = [QPersistentModelIndex(i) for i in to_remove]
        for pidx in persistent:
            if pidx.isValid():
                self._model.remove_row(
                    self._model.index(pidx.row(), 0, pidx.parent())
                )

    def _on_move_up(self):
        """Move the selected node one row up and follow the selection."""
        proxy_idx = self._tree.currentIndex()
        if not proxy_idx.isValid():
            return
        index = self._map_src(proxy_idx)
        row = index.row()
        self._model.move_up(index)
        parent = index.parent()
        self._tree.setCurrentIndex(
            self._map_proxy(self._model.index(row - 1, 0, parent))
        )

    def _on_move_down(self):
        """Move the selected node one row down and follow the selection."""
        proxy_idx = self._tree.currentIndex()
        if not proxy_idx.isValid():
            return
        index = self._map_src(proxy_idx)
        row = index.row()
        self._model.move_down(index)
        parent = index.parent()
        self._tree.setCurrentIndex(
            self._map_proxy(self._model.index(row + 1, 0, parent))
        )

    def _on_import_dbc(self):
        """Open a file dialog and import a DBC file, showing an error on failure."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import DBC", "", "DBC Files (*.dbc);;All Files (*)")
        if not path:
            return
        try:
            self.import_dbc(path)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import DBC:\n{e}")

    def _on_export_dbc(self):
        """Open a save dialog and export the current database as a DBC file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DBC", "", "DBC Files (*.dbc);;All Files (*)")
        if not path:
            return
        if not path.endswith(".dbc"):
            path += ".dbc"
        try:
            dbc_string = self.export_dbc()
            with open(path, "w") as f:
                f.write(dbc_string)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export DBC:\n{e}")

    def _on_import_json(self):
        """Open a file dialog and import a .db.json database file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import JSON Database", "",
            "JSON Files (*.json *.db.json);;All Files (*)")
        if path:
            self.import_from_json(path)

    def import_from_json(self, path: str):
        """Import a .db.json file into the database model."""
        try:
            with open(path) as f:
                data = json.load(f)
            self._model.from_dict(data)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import JSON:\n{e}")

    def _on_export_json(self):
        """Open a save dialog and export the database model as a .json file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JSON Database", "",
            "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            with open(path, "w") as f:
                json.dump(self._model.to_dict(), f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export JSON:\n{e}")

    def _on_add_to_watch(self):
        """Add all selected signals (or signals of selected messages) to Watch.

        Signal rows contribute themselves; message rows expand to all their
        signals; file rows expand to all signals in all messages.  Duplicates
        (same can_id + name) are silently ignored.

        Emits:
            add_to_watch_requested: Once per unique signal with
                ``(can_id, name, unit, "Rx")``.
        """
        pairs = self._db_selected_signals()
        if not pairs:
            QMessageBox.information(self, "Add to Watch",
                                    "Select a message or signal.")
            return
        for can_id, name, unit in pairs:
            self.add_to_watch_requested.emit(can_id, name, unit, "Rx")

    def _on_add_to_plot(self):
        """Add all selected signals (or signals of selected messages) to Plot.

        Applies the same expansion and deduplication logic as
        :meth:`_on_add_to_watch`.

        Emits:
            add_to_plot_requested: Once per unique signal with
                ``(can_id, name, unit)``.
        """
        pairs = self._db_selected_signals()
        if not pairs:
            QMessageBox.information(self, "Add to Plot",
                                    "Select a message or signal.")
            return
        for can_id, name, unit in pairs:
            self.add_to_plot_requested.emit(can_id, name, unit)

    def _on_add_to_tx(self):
        """Add all selected messages to the TX list.

        Message rows are added directly; signal rows resolve to their parent
        message; file rows expand to all messages.  Duplicates (same can_id)
        are skipped.  Uses the source file's bus number for each TX entry.

        Emits:
            add_to_tx_requested: Once per unique message with
                ``(can_id, dlc, is_extended, name, cycle_ms, bus)``.
        """
        messages = self._db_selected_messages()
        if not messages:
            QMessageBox.information(self, "Add to TX", "Select a message first.")
            return
        for args in messages:
            self.add_to_tx_requested.emit(*args)

    # -- DBC import/export --

    def import_dbc(self, path: str):
        """Import a DBC file, append its contents to the model, and emit ``dbc_imported``.

        Args:
            path: Absolute path to the ``.dbc`` file to import.

        Emits:
            dbc_imported: With the imported file path on success.
        """
        db = cantools.database.Database()
        db.add_dbc_file(path)
        filename = Path(path).name
        self._model.import_from_cantools(db, filename=filename,
                                         source_path=path, append=True)
        self._resize_columns()
        self.dbc_imported.emit(path)

    def import_dbc_silent(self, path: str):
        """Import a DBC without emitting dbc_imported (used on project load)."""
        db = cantools.database.Database()
        db.add_dbc_file(path)
        filename = Path(path).name
        self._model.import_from_cantools(db, filename=filename,
                                         source_path=path, append=True)
        self._resize_columns()

    def remove_dbc(self, path: str):
        """Remove the file-level database entry matching path."""
        self._model.remove_by_source_path(path)

    def export_dbc(self) -> str:
        """Export the current database as a DBC-format string.

        Returns:
            DBC content as a text string suitable for writing to a ``.dbc`` file.
        """
        db = self._model.export_to_cantools()
        return db.as_dbc_string()

    # -- Serialization --

    def to_dict(self) -> list[dict]:
        """Serialize the entire database model to a list of file-entry dicts.

        Returns:
            List of dicts representing all file entries, for project persistence.
        """
        return self._model.to_dict()

    def manual_databases_to_dict(self) -> list[dict]:
        """Return only manually created databases (not imported from DBC)."""
        return self._model.to_dict(manual_only=True)

    def from_dict(self, data: list[dict]):
        """Load a complete database from a list of file-entry dicts.

        Replaces existing model contents and resizes columns afterward.

        Args:
            data: List of file-entry dicts as returned by :meth:`to_dict`.
        """
        self._model.from_dict(data)
        self._resize_columns()

    def append_from_dict(self, data: list[dict]):
        """Append file entries from dict without clearing existing data."""
        self._model.append_from_dict(data)
        self._resize_columns()
