Message Data Flow
=================

End-to-end RX flow
------------------

.. code-block:: text

    ┌──────────────────────────────────────────────────────────────────┐
    │  CAN bus (hardware / vcan)                                       │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │  python-can Bus.recv()
    ┌───────────────────────────────▼──────────────────────────────────┐
    │  CanReceiver (worker thread)                                     │
    │  • Blocks on recv(timeout=10 ms)                                 │
    │  • Accumulates frames in a local list                            │
    │  • Emits message_received(list[CanMessage]) every 20 ms or       │
    │    when 500 messages accumulate                                   │
    └───────────────────────────────┬──────────────────────────────────┘
                   Qt queued signal │ (crosses thread boundary)
    ┌───────────────────────────────▼──────────────────────────────────┐
    │  CanService (main thread)                                        │
    │  • Passes batch to MessageDispatcher.dispatch_batch()            │
    └───────────────────────────────┬──────────────────────────────────┘
                                    │
    ┌───────────────────────────────▼──────────────────────────────────┐
    │  MessageDispatcher (main thread)                                 │
    │  • Optionally applies rx_hook (ScriptPlugin.apply_rx)            │
    │    — can modify or drop any frame                                │
    │  • Emits messages_received(list[CanMessage])                     │
    └────────┬────────────┬─────────────┬──────────────┬──────────────┘
             │            │             │              │
             ▼            ▼             ▼              ▼
        RxMessageModel  WatchModel  TraceModel  PlotDataService
          (50+200 ms)   (100 ms)   (100+100 ms)  (50 ms)
                                       │              │
                                       ▼              ▼
                                  _DiskWriterThread  PlotWindow
                                  + Ring Buffer      (50 ms render)
                                  + Trace Store


Dual dispatch paths
-------------------

``MessageDispatcher`` exposes two signal paths:

``messages_received`` (batch)
  Emitted by ``dispatch_batch()`` which is called by ``CanReceiver``
  (and ``PlotTraceService`` on file playback).  All real-time consumers
  connect here.

``message_received`` (single)
  Emitted by ``dispatch()`` which is called by ``TracePlayer`` during
  trace replay.  Replay is inherently single-frame because the player
  controls timing by sleeping between emissions.

Models connect to *both* signals:
``on_messages(list)`` handles the batch path;
``on_message(msg)`` handles the single-frame path from the player.


RX filter gate
--------------

``RxFilterModel`` is checked inside ``RxMessageModel.on_messages()``
before any frame is accepted into the ``_pending`` list.
``WatchModel`` and ``TraceModel`` do **not** consult the filter — they
receive all frames so the trace file is complete and watch signals from
any bus are available.

Signal decode flow
------------------

Decoded signal values flow through two separate paths:

**RxMessageModel → child signal rows (tree view)**
  Every 200 ms, ``_update_signals()`` calls ``SignalDecoder.decode()``
  on rows that have received new data since the last decode cycle.
  Decoded ``SignalItem`` objects are stored in ``RxMessageItem.signals``
  and surfaced as child rows in the tree view.

**RxMessageModel → WatchModel (explicit subscription)**
  Signals are not automatically forwarded.  The user explicitly adds a
  signal to the Watch list via the context menu or the "Add to Watch"
  button.  Each ``WatchEntry`` stores ``(arb_id, signal_name)``; the
  ``WatchModel`` decodes fresh values when it flushes its own pending
  list (100 ms).

**RxMessageModel → PlotDataService (explicit subscription)**
  Similarly, the user adds signals to the Plot List.  ``PlotDataService``
  subscribes to ``messages_received`` and decodes only the arb-IDs that
  have at least one active plot curve.


TX flow
-------

.. code-block:: text

    TxMessageModel (main thread)
    │  User edits data / enables cycle
    │  model.dataChanged ──► CanTransmitter._mark_stale()
    │  model.snapshot_requested ◄── CanTransmitter (queued)
    │  _build_snapshot() ──────────► _TxSnapshot list (immutable)
    │                                        │
    ▼                                        ▼
    CanTransmitter (worker thread)
    │  Reads self._snapshot (reference swap, no lock needed)
    │  Checks per-row cycle timer
    │  Calls send_func(CanMessage) on time
    │
    ▼
    MainWindow._send_message()   ← runs on main thread via signal
    │  Optionally applies ScriptPlugin.apply_tx()
    │
    ▼
    CanBus.send()  →  CAN bus hardware


UDS flow
--------

.. code-block:: text

    DiagnosticWindow / WatchDidWindow / DtcWindow
    │  User triggers action (Read DID, Change Session, etc.)
    │
    ▼
    UdsService.request()
    │  Enqueues command in UdsWorker._queue (SimpleQueue)
    │
    ▼
    UdsWorker (worker thread)
    │  Calls udsoncan client (blocking)
    │  Emits response_received(UdsResponse) signal
    │
    ▼  (Qt queued delivery)
    UdsService.response_received  →  DiagnosticWindow / WatchDidWindow / DtcWindow
