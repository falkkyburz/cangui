Layered Architecture Overview
==============================

cangui is organized into five layers.  The dependency arrows always point
downward — upper layers import from lower layers, never the reverse.

.. code-block:: text

    ┌─────────────────────────────────────────────────────┐
    │  UI Layer          ui_*.py, app.py, theme.py         │
    │  (QMainWindow, BaseDockWindow subclasses)            │
    └──────────────────────────┬──────────────────────────┘
                               │ creates / connects
    ┌──────────────────────────▼──────────────────────────┐
    │  Worker Layer      worker_*.py                       │
    │  (QThread — CAN recv, TX, trace play, UDS)           │
    └──────────────────────────┬──────────────────────────┘
                               │ emits Qt signals
    ┌──────────────────────────▼──────────────────────────┐
    │  Service Layer     service_*.py                      │
    │  (dispatcher, plot data, CAN connections, workspace) │
    └──────────────────────────┬──────────────────────────┘
                               │ feeds
    ┌──────────────────────────▼──────────────────────────┐
    │  Model Layer       model_*.py                        │
    │  (QAbstractItemModel / QAbstractTableModel)          │
    └──────────────────────────┬──────────────────────────┘
                               │ uses
    ┌──────────────────────────▼──────────────────────────┐
    │  Core Layer        can_bus, can_message, options,    │
    │                    project, signal_decoder, database │
    │  (Pure Python — NO Qt imports in can_bus/options)    │
    └─────────────────────────────────────────────────────┘

Layer responsibilities
----------------------

Core
  Owns all data structures and platform abstractions with no UI
  dependency.  ``CanBus`` wraps python-can; ``CanMessage`` is the
  application's canonical frame representation; ``AppOptions`` persists
  global settings; ``Project`` manages the project file.  **No Qt
  imports are allowed in** ``can_bus.py`` **or** ``options.py``.

Service
  Business-logic orchestrators that live on the main thread.
  ``MessageDispatcher`` fans out incoming frames to all consumers.
  ``PlotDataService`` accumulates signal samples and down-samples them
  for display.  ``CanService`` owns the list of active connections and
  their ``CanReceiver`` threads.

Model
  Qt item models (``QAbstractItemModel`` / ``QAbstractTableModel``)
  that connect the data to tree and table views.  Models accumulate
  incoming messages in a ``_pending`` list and flush them on a QTimer
  to decouple the high-frequency CAN bus from the 60 fps UI render
  rate.  See :doc:`threading` for timer intervals.

Worker
  ``QThread`` subclasses that perform blocking I/O (CAN recv, disk
  writes) or time-sensitive loops (TX cyclic, UDS request/response)
  off the main thread.  Workers communicate with the rest of the
  application exclusively via Qt signals.

UI
  ``MainWindow`` (``QMainWindow``) hosts three ``QTabWidget`` panes
  separated by ``QSplitter``.  Every panel is a subclass of
  :class:`~cangui.ui_base_dock_window.BaseDockWindow`.


Module-to-layer mapping
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 40 20 40

   * - Module(s)
     - Layer
     - Responsibility
   * - ``can_bus``, ``can_message``, ``options``, ``project``
     - Core
     - CAN bus abstraction, message type, settings, project I/O
   * - ``signal_decoder``, ``database_manager``, ``dbc_manager``, ``odx_manager``
     - Core
     - DBC/ODX decode and encode
   * - ``service_can``, ``service_message_dispatcher``
     - Service
     - Connection lifecycle, frame fan-out
   * - ``service_plot_data``, ``service_plot_trace``
     - Service
     - Signal buffering and LTTB downsampling
   * - ``service_uds``, ``service_workspace``
     - Service
     - UDS session management, layout persistence
   * - ``model_rx_message``, ``model_tx_message``, ``model_trace``, ``model_watch``
     - Model
     - RX display, TX editing, trace log, signal watch
   * - ``model_rx_filter``, ``model_plot_list``, ``model_connection``, ``model_database``, ``model_project``
     - Model
     - Filter rules, plot signal list, connections, DB editor, project tree
   * - ``worker_can_receiver``, ``worker_can_transmitter``
     - Worker
     - Blocking CAN recv/TX loop in background threads
   * - ``worker_trace_player``, ``worker_uds``
     - Worker
     - Trace replay, UDS request/response sequencing
   * - ``ui_main_window``, ``ui_base_dock_window``, ``ui_*.py``
     - UI
     - 3-pane layout, all dock panels
   * - ``trace_writer``, ``trace_reader``, ``trace_writer_blf``
     - Utility
     - TRC/BLF file format I/O
   * - ``script_plugin``, ``security_loader``, ``uds_client``, ``dtc_manager``
     - Utility
     - Extensibility and diagnostic helpers


Dependency rules
----------------

* **Core has no Qt.**  ``can_bus`` and ``options`` use only the Python
  standard library.  ``signal_decoder`` and the database managers may
  import ``cantools`` / ``odxtools`` but not PySide6.
* **Workers never touch UI directly.**  They emit signals; Qt delivers
  them to the main thread via the event loop.
* **Models never call workers.**  Data flows one way: Worker → Service →
  Model → View.
* **Services are not singletons.**  They are instantiated once in
  ``MainWindow.__init__`` and injected into models and UI as constructor
  arguments.
