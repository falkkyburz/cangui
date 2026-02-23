"""Qt item model for displaying received CAN messages in a tree-table view.

The model uses a two-level tree structure:

- Root (top-level) rows represent unique CAN messages, keyed by (bus, arb_id).
- Child rows beneath each message show the decoded signal values extracted
  from the message payload via the signal decoder.

Internal pointer encoding
-------------------------
QAbstractItemModel requires each QModelIndex to carry an opaque ``internalId``
(or ``internalPointer``) that identifies the item's parent.  This module uses
a simple integer scheme:

- Top-level (message) rows: ``internalId == 0`` (``_TOP_LEVEL``).
- Child (signal) rows: ``internalId == parent_row + 1``.

Using ``parent_row + 1`` guarantees that the value is always non-zero, so it
can be unambiguously distinguished from the top-level sentinel.

Batching strategy
-----------------
Two QTimers decouple incoming CAN traffic from Qt view refreshes:

1. **50 ms flush timer** (``_batch_timer``): drains ``_pending`` into
   ``_items``, emitting ``dataChanged`` for updated rows and
   ``beginInsertRows``/``endInsertRows`` for new ones.

2. **200 ms signal timer** (``_signal_timer``): runs the slower signal
   decoding step only for rows marked dirty by the flush, then emits
   ``dataChanged`` for the child signal rows so the view repaints them.
"""

import time
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QTimer
from PySide6.QtGui import QColor

from cangui.can_message import CanMessage
from cangui.signal_decoder import SignalDecoder
from cangui.model_rx_filter import RxFilterModel

# Internal ID encoding:
#   Top-level (message) rows: internalId = 0
#   Child (signal) rows:      internalId = parent_row + 1
_TOP_LEVEL = 0


@dataclass
class SignalItem:
    """Display data for a single decoded CAN signal (child row).

    Attributes:
        name: Signal name as defined in the DBC/ODX database.
        value: Decoded value formatted as a display string.
        unit: Physical unit string (e.g. ``"km/h"``), or empty string if none.
        is_multiplexer: True when this signal is the mux selector for the message.
        multiplexer_ids: List of mux ID values this signal belongs to, or None
            when the signal is not multiplexed.
        last_update: Monotonic timestamp of the most recent value change; used
            for the 1.5 s fade-in highlight effect.
    """

    name: str = ""
    value: str = ""
    unit: str = ""
    is_multiplexer: bool = False
    multiplexer_ids: list[int] | None = None
    last_update: float = -1000.0


@dataclass
class RxMessageItem:
    """State for a single received CAN message (top-level row).

    One ``RxMessageItem`` is created per unique ``(bus, arb_id)`` pair.
    Subsequent frames with the same key update the existing item in-place.

    Attributes:
        bus: CAN channel / bus index the message arrived on.
        can_id: CAN arbitration ID (11-bit standard or 29-bit extended).
        is_extended_id: True for 29-bit extended frame IDs.
        is_error_frame: True when the frame is a CAN error frame.
        frame_type: Human-readable frame type string (e.g. ``"Data"``).
        length: Data length code (DLC) of the most recently received frame.
        symbol: DBC/ODX message name, or empty string if not found in the DB.
        raw_data: Raw payload bytes of the most recently received frame.
        timing_errors: Accumulated count of timing violations for this message.
        cycle_time_ms: Exponentially-smoothed inter-frame period in milliseconds.
            Uses an 80/20 EMA: ``new = old * 0.8 + measured * 0.2``.
        count: Total number of frames received for this message since last clear.
        last_timestamp: Timestamp (seconds, from python-can) of the most recent frame.
        signals: Decoded signal child items; empty until the decoder runs.
    """

    bus: int = 0
    can_id: int = 0
    is_extended_id: bool = False
    is_error_frame: bool = False
    frame_type: str = ""
    length: int = 0
    symbol: str = ""
    raw_data: bytes = b""
    timing_errors: int = 0
    cycle_time_ms: float = 0.0
    count: int = 0
    last_timestamp: float = 0.0
    last_seen_monotonic: float = field(default_factory=time.monotonic)
    signals: list[SignalItem] = field(default_factory=list)


COLUMNS = ["Bus", "ID (hex)", "Ext", "Type", "Length", "Symbol",
           "Data (hex)", "Timing Errors", "Cycle Time", "Count"]

STALE_TIMEOUT_S = 5.0   # seconds with no update before a row is greyed out
_STALE_COLOR = QColor("#808080")


def _mux_prefix(sig: SignalItem) -> str:
    """Return the multiplexer prefix string for a signal's display name.

    Args:
        sig: The signal item whose multiplexer role determines the prefix.

    Returns:
        ``"[M] "`` if ``sig`` is the mux selector, ``"[m<id>] "`` if it is a
        muxed signal (showing its mux IDs), or ``""`` for non-muxed signals.
    """
    if sig.is_multiplexer:
        return "[M] "
    if sig.multiplexer_ids is not None:
        return "[m" + ",".join(str(i) for i in sig.multiplexer_ids) + "] "
    return ""


class RxMessageModel(QAbstractItemModel):
    """Tree-table model for received CAN messages, backed by a batched update pipeline.

    The model maintains a list of ``RxMessageItem`` objects, one per unique
    ``(bus, arbitration_id)`` pair.  Each item may have zero or more
    ``SignalItem`` children populated by the ``SignalDecoder``.

    Incoming ``CanMessage`` objects are appended to ``_pending`` by the
    dispatcher thread via ``on_message`` / ``on_messages``.  A 50 ms QTimer
    drains ``_pending`` into ``_items`` on the main thread (``_flush``), and a
    200 ms QTimer re-runs signal decoding for dirty rows (``_update_signals``).

    Note:
        All public slots and methods must be called from the Qt main thread.
        ``on_messages`` is safe to call from any thread because it only appends
        to a Python list, which is protected by the GIL; however callers should
        treat this as an implementation detail and prefer emitting signals that
        deliver to the main thread via Qt's queued connection mechanism.
    """

    def __init__(self, decoder: SignalDecoder | None = None,
                 rx_filter: RxFilterModel | None = None, parent=None):
        """Initialise the model and start the background timers.

        Args:
            decoder: Optional ``SignalDecoder`` used to resolve symbol names and
                decode signal values from raw CAN payloads.  Can be set later
                via ``set_decoder``.
            rx_filter: Optional ``RxFilterModel`` that gates which incoming
                messages are accepted.  Can be set later via ``set_filter``.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._items: list[RxMessageItem] = []
        self._id_to_row: dict[tuple[int, int], int] = {}  # (bus, arb_id) -> row
        self._pending: list[CanMessage] = []
        self._decoder = decoder
        self._filter = rx_filter

        self._rows_needing_decode: set[int] = set()

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(100)
        self._batch_timer.timeout.connect(self._flush)
        self._batch_timer.start()

        self._signal_timer = QTimer(self)
        self._signal_timer.setInterval(200)
        self._signal_timer.timeout.connect(self._update_signals)
        self._signal_timer.start()

        # Stale detection: check every 500 ms and grey out rows not updated in 5 s
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(500)
        self._stale_timer.timeout.connect(self._check_stale)
        self._stale_timer.start()

        # Signal highlight: repaint fading green within 1.5 s of a value change
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(100)
        self._highlight_timer.timeout.connect(self._refresh_signal_highlights)
        self._highlight_timer.start()

    def set_decoder(self, decoder: SignalDecoder):
        """Replace the signal decoder used for symbol resolution and decoding.

        Args:
            decoder: New ``SignalDecoder`` instance to use going forward.
                Existing decoded signal data is not immediately refreshed;
                call ``refresh_symbols`` to repopulate the displayed values.
        """
        self._decoder = decoder

    def index(self, row, column, parent=QModelIndex()):
        """Return a model index for the given row, column, and parent.

        Implements the ``QAbstractItemModel`` pure virtual.  The ``internalId``
        of the created index encodes the parent relationship:

        - Top-level rows get ``internalId = _TOP_LEVEL`` (0).
        - Child rows get ``internalId = parent.row() + 1``.

        Args:
            row: Row number relative to ``parent``.
            column: Column number.
            parent: Parent index; an invalid index means top-level.

        Returns:
            A valid ``QModelIndex`` if the position exists, otherwise an
            invalid ``QModelIndex``.
        """
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            # Top-level message row
            return self.createIndex(row, column, _TOP_LEVEL)
        else:
            # Child signal row — encode parent's row
            return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index: QModelIndex):
        """Return the parent index of the given model index.

        Implements the ``QAbstractItemModel`` pure virtual.

        Args:
            index: The index whose parent is requested.

        Returns:
            The parent ``QModelIndex``.  Returns an invalid index for top-level
            rows (i.e. items whose ``internalId`` is ``_TOP_LEVEL``).
        """
        if not index.isValid():
            return QModelIndex()
        ptr = index.internalId()
        if ptr == _TOP_LEVEL:
            # Already a top-level row
            return QModelIndex()
        # Child row — return parent as a top-level index
        parent_row = ptr - 1
        return self.createIndex(parent_row, 0, _TOP_LEVEL)

    def rowCount(self, parent=QModelIndex()):
        """Return the number of rows under the given parent.

        Implements the ``QAbstractItemModel`` pure virtual.

        Args:
            parent: Parent index.  An invalid parent means the invisible root,
                so the method returns the number of top-level message rows.
                For a valid top-level index the number of decoded signal child
                rows is returned.  Child rows themselves have no further
                children.

        Returns:
            Row count for the given level, or 0 if ``parent`` is a child row.
        """
        if not parent.isValid():
            return len(self._items)
        # Only top-level items have children
        if parent.internalId() == _TOP_LEVEL and 0 <= parent.row() < len(self._items):
            return len(self._items[parent.row()].signals)
        return 0

    def columnCount(self, parent=QModelIndex()):
        """Return the number of columns (always the full ``COLUMNS`` width).

        Implements the ``QAbstractItemModel`` pure virtual.

        Args:
            parent: Ignored; the column count is the same at every tree level.

        Returns:
            Number of columns defined in the ``COLUMNS`` constant.
        """
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return horizontal header label strings for the column titles.

        Implements the ``QAbstractItemModel`` virtual.

        Args:
            section: Column index (0-based).
            orientation: Only ``Qt.Orientation.Horizontal`` returns data;
                vertical headers are not used.
            role: Only ``Qt.ItemDataRole.DisplayRole`` returns data.

        Returns:
            The column title string, or ``None`` for unsupported roles /
            orientations.
        """
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def _is_top_level(self, index: QModelIndex) -> bool:
        """Return True if ``index`` refers to a top-level message row.

        Args:
            index: The index to test.

        Returns:
            True when ``index.internalId() == _TOP_LEVEL``.
        """
        return index.internalId() == _TOP_LEVEL

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        """Return display data, check-state, or foreground colour for a cell.

        Implements the ``QAbstractItemModel`` pure virtual.

        Top-level rows expose all ``COLUMNS`` fields of ``RxMessageItem``.
        Child (signal) rows populate only columns 5 (Symbol/name) and 6
        (Data/value+unit); all other columns return ``None``.

        Special role handling:

        - ``ForegroundRole``: error frames are coloured red.
        - ``CheckStateRole``: column 2 ("Ext") renders as a checkbox reflecting
          ``RxMessageItem.is_extended_id``.
        - ``DisplayRole``: all other display data.

        Args:
            index: Cell to query.
            role: Qt item data role.

        Returns:
            The cell value appropriate for ``role``, or ``None`` when the role
            is not supported or the index is out of range.
        """
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if 0 <= index.row() < len(self._items) or not self._is_top_level(index):
                # Resolve item for both top-level and child rows
                if self._is_top_level(index):
                    if index.row() < len(self._items):
                        item = self._items[index.row()]
                    else:
                        return None
                else:
                    parent_row = index.internalId() - 1
                    if 0 <= parent_row < len(self._items):
                        item = self._items[parent_row]
                    else:
                        return None
                if item.is_error_frame:
                    return QColor(Qt.GlobalColor.red)
                elapsed = time.monotonic() - item.last_seen_monotonic
                if elapsed > STALE_TIMEOUT_S:
                    return _STALE_COLOR
            return None

        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole:
            if self._is_top_level(index) and col == 2 and 0 <= index.row() < len(self._items):
                item = self._items[index.row()]
                return Qt.CheckState.Checked if item.is_extended_id else Qt.CheckState.Unchecked
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            if not index.parent().isValid() and 0 <= index.row() < len(self._items):
                item = self._items[index.row()]
                elapsed = time.monotonic() - item.last_seen_monotonic
                if elapsed > STALE_TIMEOUT_S:
                    return f"Last seen {elapsed:.1f} s ago"
            return None

        if role == Qt.ItemDataRole.BackgroundRole:
            if not self._is_top_level(index):
                parent_row = index.internalId() - 1
                if 0 <= parent_row < len(self._items):
                    sigs = self._items[parent_row].signals
                    if 0 <= index.row() < len(sigs):
                        elapsed = time.monotonic() - sigs[index.row()].last_update
                        if elapsed < 1.5:
                            alpha = max(0, int(180 * (1.0 - elapsed / 1.5)))
                            return QColor(100, 200, 100, alpha)
            return None

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if self._is_top_level(index):
            if index.row() >= len(self._items):
                return None
            item = self._items[index.row()]
            match col:
                case 0: return item.bus
                case 1:
                    if item.is_extended_id:
                        return f"{item.can_id:08X}"
                    return f"{item.can_id:03X}"
                case 2: return None  # checkbox only
                case 3: return item.frame_type
                case 4: return item.length
                case 5: return item.symbol
                case 6: return " ".join(f"{b:02X}" for b in item.raw_data[:item.length])
                case 7: return item.timing_errors if item.timing_errors else ""
                case 8: return f"{item.cycle_time_ms:.1f}" if item.cycle_time_ms else ""
                case 9: return item.count
        else:
            parent_row = index.internalId() - 1
            if parent_row >= len(self._items):
                return None
            sigs = self._items[parent_row].signals
            if index.row() >= len(sigs):
                return None
            sig = sigs[index.row()]
            match col:
                case 5: return _mux_prefix(sig) + sig.name
                case 6:
                    if sig.unit:
                        return f"{sig.value} {sig.unit}"
                    return sig.value
        return None

    def flags(self, index: QModelIndex):
        """Return item flags; all cells are enabled and selectable but not editable.

        Implements the ``QAbstractItemModel`` virtual.  The RX model is
        read-only so no ``ItemIsEditable`` flag is set.

        Args:
            index: The cell whose flags are requested.

        Returns:
            ``ItemIsEnabled | ItemIsSelectable`` for every valid cell.
        """
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_filter(self, rx_filter: RxFilterModel):
        """Replace the active RX filter.

        Messages that do not pass the filter are silently dropped in
        ``on_message`` / ``on_messages``.  Changing the filter does not
        retroactively remove already-displayed rows.

        Args:
            rx_filter: New filter model to apply to incoming messages.
        """
        self._filter = rx_filter

    def on_message(self, msg: CanMessage):
        """Accept a single incoming CAN message and append it to the pending queue.

        Messages are not processed immediately; they are batched until the
        50 ms flush timer fires.  Non-RX messages and messages rejected by the
        active filter are silently discarded.

        Args:
            msg: The incoming CAN message to process.

        Note:
            Safe to call from any thread; list ``append`` is GIL-protected.
            The actual model mutation happens on the main thread inside
            ``_flush``.
        """
        if not msg.is_rx:
            return
        if self._filter and not self._filter.accepts(msg.arbitration_id, msg.bus):
            return
        self._pending.append(msg)

    def on_messages(self, messages: list[CanMessage]):
        """Accept a batch of incoming CAN messages and append them to the pending queue.

        Equivalent to calling ``on_message`` for each item but avoids per-call
        filter attribute lookup overhead.  Non-RX messages and filter-rejected
        messages are silently discarded.

        Args:
            messages: List of incoming CAN messages delivered by the dispatcher.

        Note:
            Safe to call from any thread; list operations are GIL-protected.
            Model mutations are deferred to the main thread via ``_flush``.
        """
        filt = self._filter
        for msg in messages:
            if not msg.is_rx:
                continue
            if filt and not filt.accepts(msg.arbitration_id, msg.bus):
                continue
            self._pending.append(msg)

    def _decode_signals(self, item: RxMessageItem):
        """Decode signals from raw data using the signal decoder and update the item.

        If the decoder is absent or returns no results the item is left
        unchanged.  When the signal count is the same as before, existing
        ``SignalItem`` objects are updated in-place to avoid churn in the child
        list (which would require ``beginRemoveRows`` / ``beginInsertRows``
        round-trips).  When the count differs the list is replaced wholesale.

        Args:
            item: The ``RxMessageItem`` whose ``raw_data`` will be decoded.
                  ``item.signals`` and ``item.symbol`` are updated in-place.
        """
        if self._decoder is None:
            return
        decoded = self._decoder.decode(item.can_id, item.raw_data)
        if not decoded:
            return

        if not item.symbol:
            item.symbol = self._decoder.get_symbol(item.can_id)

        new_signals = []
        for ds in decoded:
            new_signals.append(SignalItem(
                name=ds.name,
                value=ds.display_value,
                unit=ds.unit,
                is_multiplexer=ds.is_multiplexer,
                multiplexer_ids=ds.multiplexer_ids,
            ))

        old_count = len(item.signals)
        new_count = len(new_signals)

        now = time.monotonic()
        if old_count == new_count:
            for i, sig in enumerate(new_signals):
                old = item.signals[i]
                old.name = sig.name
                if old.value != sig.value:
                    old.last_update = now
                old.value = sig.value
                old.unit = sig.unit
                old.is_multiplexer = sig.is_multiplexer
                old.multiplexer_ids = sig.multiplexer_ids
        else:
            for sig in new_signals:
                sig.last_update = now
            item.signals = new_signals

    def _flush(self):
        """Fast timer (50 ms): drain ``_pending`` into ``_items`` and notify the view.

        For each message in the batch:

        - **Known key** ``(bus, arb_id)``: updates the existing ``RxMessageItem``
          in-place (raw data, DLC, count, and the exponentially-smoothed cycle
          time) and marks the row dirty for signal decoding.
        - **New key**: inserts a new ``RxMessageItem`` at the end of ``_items``,
          immediately decodes its signals, and notifies the view via
          ``beginInsertRows`` / ``endInsertRows``.

        After processing, a single ``dataChanged`` covering the modified row
        range is emitted for updated rows, and all updated rows are added to
        ``_rows_needing_decode`` for the slower signal-decode timer.

        Emits:
            dataChanged: For the bounding rectangle of all updated message rows.
            beginInsertRows / endInsertRows: For each newly seen message.

        Note:
            Runs on the main thread (called by ``_batch_timer``).  The swap of
            ``_pending`` to a local variable is safe because Python list
            assignment is atomic under the GIL.
        """
        if not self._pending:
            return
        batch = self._pending
        self._pending = []

        rows_to_update: set[int] = set()

        for msg in batch:
            key = (msg.bus, msg.arbitration_id)
            row = self._id_to_row.get(key)
            if row is not None:
                item = self._items[row]
                now = msg.timestamp
                if item.last_timestamp > 0:
                    dt = (now - item.last_timestamp) * 1000
                    if item.cycle_time_ms > 0:
                        item.cycle_time_ms = item.cycle_time_ms * 0.8 + dt * 0.2
                    else:
                        item.cycle_time_ms = dt
                item.raw_data = msg.data
                item.length = msg.dlc
                item.count += 1
                item.last_timestamp = now
                item.last_seen_monotonic = time.monotonic()
                rows_to_update.add(row)
            else:
                new_row = len(self._items)
                self.beginInsertRows(QModelIndex(), new_row, new_row)
                item = RxMessageItem(
                    bus=msg.bus,
                    can_id=msg.arbitration_id,
                    is_extended_id=msg.is_extended_id,
                    is_error_frame=msg.is_error_frame,
                    frame_type=msg.frame_type,
                    length=msg.dlc,
                    raw_data=msg.data,
                    count=1,
                    last_timestamp=msg.timestamp,
                )
                if self._decoder:
                    item.symbol = self._decoder.get_symbol(msg.arbitration_id)
                self._decode_signals(item)
                self._items.append(item)
                self._id_to_row[key] = new_row
                self.endInsertRows()

        if rows_to_update:
            min_row = min(rows_to_update)
            max_row = max(rows_to_update)
            self.dataChanged.emit(
                self.index(min_row, 0),
                self.index(max_row, self.columnCount() - 1),
            )
            self._rows_needing_decode.update(rows_to_update)

    def _update_signals(self):
        """Slow timer (200 ms): re-decode signals for dirty rows and notify child views.

        Consumes ``_rows_needing_decode`` (populated by ``_flush``), runs
        ``_decode_signals`` for each dirty row, and emits ``dataChanged`` for
        the full child signal range of that row so the view refreshes the
        signal values without affecting the top-level row display.

        Emits:
            dataChanged: For the full child-row range of each dirty message row,
                with the parent ``QModelIndex`` set so only signal children are
                invalidated.

        Note:
            Runs on the main thread (called by ``_signal_timer``).
        """
        if not self._rows_needing_decode:
            return
        dirty = self._rows_needing_decode
        self._rows_needing_decode = set()

        for row in dirty:
            if row >= len(self._items):
                continue
            item = self._items[row]
            self._decode_signals(item)
            if item.signals:
                parent_idx = self.index(row, 0)
                self.dataChanged.emit(
                    self.index(0, 0, parent_idx),
                    self.index(len(item.signals) - 1,
                               self.columnCount() - 1, parent_idx),
                )

    def _check_stale(self):
        """Periodic timer (500 ms): emit dataChanged for rows that just became stale.

        A row is considered stale when ``time.monotonic() - item.last_seen_monotonic``
        exceeds ``STALE_TIMEOUT_S``.  Only rows that have *crossed* the threshold
        since the last check emit ``dataChanged`` so we don't flood the view.
        """
        if not self._items:
            return
        now = time.monotonic()
        stale_rows = []
        for row, item in enumerate(self._items):
            elapsed = now - item.last_seen_monotonic
            # Check whether the row crossed the stale boundary since last poll.
            # We re-emit within a small window around the threshold so the colour
            # change fires once rather than every 500 ms.
            if STALE_TIMEOUT_S <= elapsed < STALE_TIMEOUT_S + 0.6:
                stale_rows.append(row)
        for row in stale_rows:
            top_left = self.index(row, 0)
            bot_right = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bot_right)
            # Also refresh signal children
            item = self._items[row]
            if item.signals:
                parent_idx = self.index(row, 0)
                self.dataChanged.emit(
                    self.index(0, 0, parent_idx),
                    self.index(len(item.signals) - 1, self.columnCount() - 1, parent_idx),
                )

    def clear_errors(self):
        """Remove all error-frame rows from the model.

        Called after a bus reset so that error frames are cleared while normal
        message history is preserved.

        Emits:
            beginResetModel / endResetModel: Notifies attached views.
        """
        if not any(item.is_error_frame for item in self._items):
            return
        self.beginResetModel()
        self._items = [item for item in self._items if not item.is_error_frame]
        self._id_to_row = {(item.bus, item.can_id): i for i, item in enumerate(self._items)}
        self.endResetModel()

    def _refresh_signal_highlights(self):
        """Periodic timer (100 ms): repaint signal rows whose highlight is still active.

        Emits dataChanged for each parent row that has at least one signal with a
        highlight younger than 1.5 s so the fade-in green effect updates smoothly.
        """
        if not self._items:
            return
        now = time.monotonic()
        for row, item in enumerate(self._items):
            if not item.signals:
                continue
            if any(now - sig.last_update < 1.5 for sig in item.signals):
                parent_idx = self.index(row, 0)
                self.dataChanged.emit(
                    self.index(0, 0, parent_idx),
                    self.index(len(item.signals) - 1, self.columnCount() - 1, parent_idx),
                )

    def clear(self):
        """Remove all messages and reset all internal state.

        Emits:
            beginResetModel / endResetModel: Notifies attached views that the
                entire model has been repopulated from scratch.
        """
        self.beginResetModel()
        self._items.clear()
        self._id_to_row.clear()
        self._pending.clear()
        self._rows_needing_decode.clear()
        self.endResetModel()

    def get_item(self, index: QModelIndex) -> RxMessageItem | None:
        """Return the ``RxMessageItem`` associated with any valid index.

        Works for both top-level message rows and signal child rows (in which
        case the parent message item is returned).

        Args:
            index: Any valid model index.

        Returns:
            The ``RxMessageItem`` for the row (or its parent if ``index`` is a
            child row), or ``None`` if ``index`` is invalid or out of range.
        """
        if not index.isValid():
            return None
        if self._is_top_level(index):
            if 0 <= index.row() < len(self._items):
                return self._items[index.row()]
        else:
            parent_row = index.internalId() - 1
            if 0 <= parent_row < len(self._items):
                return self._items[parent_row]
        return None

    def get_signal_at(self, index: QModelIndex) -> tuple[RxMessageItem, SignalItem] | None:
        """Return the ``(RxMessageItem, SignalItem)`` pair for a signal child index.

        Args:
            index: A model index that is expected to be a child (signal) row.
                Top-level indices and invalid indices return ``None``.

        Returns:
            A ``(message_item, signal_item)`` tuple if ``index`` points to a
            valid signal child row, otherwise ``None``.
        """
        if not index.isValid() or self._is_top_level(index):
            return None
        parent_row = index.internalId() - 1
        if 0 <= parent_row < len(self._items):
            item = self._items[parent_row]
            if 0 <= index.row() < len(item.signals):
                return item, item.signals[index.row()]
        return None

    @property
    def items(self) -> list[RxMessageItem]:
        """Read-only view of the internal message list.

        Returns:
            The live list of ``RxMessageItem`` objects in display order.
            Callers must not mutate this list directly.
        """
        return self._items

    def refresh_symbols(self):
        """Re-resolve symbol names and signals after a DBC/ODX database is loaded or removed.

        Iterates every existing ``RxMessageItem``, looks up its symbol name in
        the current decoder, and re-decodes its signals from the most recently
        stored ``raw_data``.  A single ``dataChanged`` covering all rows is
        emitted so the view repaints symbol names and signal values.

        Emits:
            dataChanged: For the full row range (all columns) of all existing
                message rows.
        """
        if self._decoder is None:
            return
        for item in self._items:
            sym = self._decoder.get_symbol(item.can_id)
            if sym:
                item.symbol = sym
            self._decode_signals(item)
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, self.columnCount() - 1),
            )
