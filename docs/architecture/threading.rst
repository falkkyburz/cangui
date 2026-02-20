Threading Model
===============

Why threading is needed
-----------------------

``python-can``'s ``Bus.recv()`` blocks the calling thread until a frame
arrives or a timeout expires.  Calling it on the Qt main thread would
freeze the UI.  Disk I/O (trace files, plot files) has unpredictable
latency that would cause dropped frames if performed synchronously.
Both concerns are solved by dedicated QThread workers.

Worker thread catalog
---------------------

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Class
     - Module
     - Role
   * - ``CanReceiver``
     - ``worker_can_receiver``
     - Calls ``Bus.recv(timeout=10 ms)`` in a tight loop; batches frames.
   * - ``CanTransmitter``
     - ``worker_can_transmitter``
     - Drives cyclic TX messages; reads an immutable snapshot.
   * - ``TracePlayer``
     - ``worker_trace_player``
     - Replays a loaded trace at a configurable speed.
   * - ``UdsWorker``
     - ``worker_uds``
     - Sequences UDS request/response pairs without blocking the UI.
   * - ``_DiskWriterThread``
     - ``model_trace`` (private)
     - Drains a ``SimpleQueue`` of write commands; owns the open file.

The batching strategy
---------------------

The central design challenge is that a CAN bus can deliver **1000+ frames
per second**, while the display can only refresh at **~60 fps**.  Feeding
every frame directly to the Qt model would call ``dataChanged`` thousands
of times per second, causing the view to repaint after every frame and
making the UI unresponsive.

cangui solves this with a two-stage pipeline:

**Stage 1 — Worker accumulation (off main thread)**

``CanReceiver`` accumulates frames in a Python list and emits a
``message_received(list[CanMessage])`` signal when either:

* 20 ms have elapsed since the last emission, **or**
* the batch contains 500 messages.

The signal crosses the thread boundary via Qt's queued connection
mechanism, which serialises the delivery to the main thread's event loop.

**Stage 2 — Model accumulation (main thread)**

Each model's ``on_messages()`` slot appends the incoming batch to
``self._pending`` — a plain Python list — without touching the Qt model
at all.  A QTimer fires periodically to drain ``_pending``, update the
internal data, and emit ``dataChanged`` once for the whole changed range.

This completely decouples CAN bus throughput from UI refresh rate.

Timer interval table
--------------------

.. list-table::
   :header-rows: 1
   :widths: 30 20 20 30

   * - Model / Service
     - Timer purpose
     - Interval
     - Notes
   * - ``RxMessageModel``
     - Flush pending messages
     - 50 ms
     - Updates frame data, counts, cycle times
   * - ``RxMessageModel``
     - Decode signals
     - 200 ms
     - Runs ``SignalDecoder`` only for visible rows
   * - ``WatchModel``
     - Flush pending messages
     - 100 ms
     - Decodes signals on flush
   * - ``TraceModel`` (fast)
     - Convert pending to ``TraceEntry``; hand off to disk writer
     - 50 ms
     - Does **not** update the view model
   * - ``TraceModel`` (slow)
     - Commit staged entries to view; emit ``dataChanged``
     - 200 ms
     - Batches ``beginInsertRows`` / ``endInsertRows`` calls
   * - ``PlotDataService``
     - Flush pending messages; trim rolling window
     - 50 ms
     - Recomputes LTTB display arrays only when new data arrived
   * - ``PlotWindow``
     - Repaint pyqtgraph curves from display arrays
     - 50 ms
     - Configurable via Settings → Plot → Update Interval
   * - ``CanTransmitter``
     - Emit accumulated TX counts to main thread
     - 200 ms
     - Batches ``counts_updated`` signal


The snapshot pattern (CanTransmitter)
--------------------------------------

The TX transmitter loop reads the TX message list at 1 kHz (1 ms sleep).
Reading a live Qt model from a worker thread is unsafe.  Instead,
``CanTransmitter`` uses a *snapshot* strategy:

1. The model emits ``dataChanged`` / ``rowsInserted`` / ``rowsRemoved``
   whenever the user edits the TX list.
2. ``CanTransmitter._mark_stale()`` sets ``_snapshot_stale = True``.
3. The worker loop notices the stale flag and emits
   ``snapshot_requested`` (a Qt signal, delivered to the main thread).
4. ``_build_snapshot()`` runs on the **main thread**, reads the model
   safely, and builds a list of frozen ``_TxSnapshot`` dataclasses (all
   fields are immutable ``bytes`` / ``int`` / ``bool``).
5. The worker loop reads ``self._snapshot`` — a plain Python reference
   swap, which is atomic at the CPython level.  No locking is needed
   because the GIL protects the reference swap, and the snapshot itself
   is immutable.

The queue pattern (DiskWriter, UdsWorker)
------------------------------------------

For strictly one-way command streams, cangui uses
``queue.SimpleQueue``, the lightest option in the stdlib (no
``task_done``/``join`` overhead):

* ``_DiskWriterThread`` receives ``("start", …)``, ``("write", …)``,
  ``("stop",)``, ``("sync", event)`` and ``("quit",)`` commands.
  Writes never block the caller; ``cmd_sync()`` blocks only when
  explicit flush confirmation is needed (e.g., before reading back a
  just-written file).
* ``UdsWorker`` queues ``(service_id, data)`` tuples; the worker deques
  them and fires the UDS request/response cycle sequentially.

Signal safety rule
------------------

**Qt signals are the ONLY mechanism for cross-thread communication.**
Workers never call methods on main-thread objects directly.  They never
write to shared lists or dicts without the GIL (which is implicit in
CPython but not guaranteed by the Qt C++ layer).  Any return value must
travel back via a signal connected with ``Qt.ConnectionType.QueuedConnection``
(the default when the sender and receiver live in different threads).

LTTB downsampling in PlotDataService
--------------------------------------

``SignalBuffer`` stores a rolling window of ``(time, value)`` samples
trimmed to ``PlotDataService.time_window`` seconds on every 50 ms flush.
The raw buffer is arbitrarily large; the *display* array is capped at
``max_display_points`` (default 5000) using the **Largest Triangle Three
Buckets** (LTTB) algorithm implemented in ``service_plot_data.lttb_downsample``.

LTTB selects the sample within each bucket that forms the largest
triangle with its neighbours, preserving peaks and valleys that matter
visually while discarding redundant flat-line samples.  This keeps the
pyqtgraph ``PlotDataItem.setData()`` call fast even at 1 kHz input.
