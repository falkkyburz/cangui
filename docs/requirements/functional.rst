Functional Requirements
=======================

This document captures the high-level functional requirements for cangui, organised by
subsystem.  Each requirement describes observable behaviour from a user or integration
perspective.

.. contents:: Subsystems
   :local:
   :depth: 1

----

RX Subsystem
------------

**Purpose**: Receive CAN frames from one or more physical or virtual bus channels and
present them to the user with decoded signal values.

Requirements:

* The application shall display every received CAN frame as a row in the RX tree-table,
  identified by its arbitration ID (decimal and hex), raw data (hex), frame counter, and
  timestamp of the most recent occurrence.
* When a DBC/KCD/ODX database is loaded, the application shall expand each message row
  with child rows showing the decoded physical value and unit of every signal defined for
  that arbitration ID.
* The RX table shall update at most once per 50 ms (flush timer) to prevent the UI from
  blocking on high-traffic buses (≥1000 msg/s).
* A ``dataChanged`` notification shall be coalesced across a 200 ms window so that Qt
  repaints are batched.
* The user shall be able to clear the RX table without disconnecting the bus.
* Frame reception shall continue uninterrupted while the user scrolls, resizes columns,
  or interacts with other panels.

----

TX Subsystem
------------

**Purpose**: Compose and transmit CAN frames, either manually (one-shot) or cyclically.

Requirements:

* The user shall be able to define TX messages by entering an arbitration ID and a raw
  data payload (hex bytes).
* When a database is loaded, TX messages shall display signal child rows that the user can
  edit; the application shall encode the entered physical value back to raw bytes.
* Each TX message shall have an optional cycle period (milliseconds).  When the period is
  non-zero and the bus is connected, the CanTransmitter worker shall transmit the message
  at that rate.
* Changes to TX message payloads or periods shall take effect within one transmitter cycle
  without restarting the worker thread.
* The transmitter shall use an immutable snapshot (frozen dataclass list) so that the UI
  thread can modify the TX model concurrently without locks.
* The user shall be able to enable/disable individual TX messages.

----

Trace Subsystem
---------------

**Purpose**: Record live CAN traffic to disk and replay recordings.

Requirements:

* The application shall support recording to PEAK TRC (text) and BLF (binary) formats.
* Recording shall begin immediately when the user clicks **Record**; the first frame shall
  be written within the current flush cycle (50 ms).
* Writing to disk shall occur on a dedicated background thread (DiskWriter) using a
  ``SimpleQueue`` so that disk latency does not affect the UI or RX model update rate.
* When a file reaches 1 GB, the recorder shall automatically roll over to a new file with
  a numeric suffix, without interrupting recording.
* Trace files shall be stored in a ``trace/`` subfolder next to the project file;
  filenames shall include an ISO 8601 timestamp prefix.
* The user shall be able to load a saved TRC or BLF file and play it back at recorded
  timestamps.  Playback shall be pauseable and stoppable.
* During playback, frames shall be dispatched through the same ``MessageDispatcher`` path
  as live frames so that the RX model, Watch model, and Plot service all update normally.

----

Plot Subsystem
--------------

**Purpose**: Display time-series graphs of decoded CAN signal values.

Requirements:

* The user shall be able to add any decoded signal to the plot by selecting it from the
  signal selector widget.
* Each signal shall be rendered as a separate curve in the pyqtgraph PlotWidget.
* The plot shall maintain a configurable rolling time window (default 10 seconds).
  Samples older than the window shall be discarded automatically.
* When the number of visible samples exceeds ``MAX_DISPLAY_POINTS`` (5000), the
  application shall apply LTTB (Largest Triangle Three Buckets) downsampling to reduce
  the point count while preserving visual peaks and valleys.
* The plot shall refresh at most once per 50 ms (QTimer) to avoid overwhelming pyqtgraph
  with continuous ``setData`` calls.
* The user shall be able to start and stop plot recording independently of bus connection
  state.  Recorded plot traces shall be saved to a ``plot/`` subfolder.
* The user shall be able to load a saved plot trace file and replay it in the plot view.

----

Database Subsystem
------------------

**Purpose**: Load signal definitions from DBC, KCD, or ODX files and use them for
decoding RX frames and encoding TX frames.

Requirements:

* The application shall load DBC and KCD files using the ``cantools`` library.
* The application shall load ODX files using the ``odxtools`` library.
* Multiple database files may be loaded simultaneously; signal definitions from all
  loaded files shall be merged for decoding.
* The user shall be able to unload individual database files without restarting.
* The application shall provide a built-in database editor allowing the user to create
  lightweight signal definitions (ID, name, start bit, length, factor, offset, unit)
  without an external DBC file.  Editor content shall be saved to a ``.db.json`` file
  alongside the project file.
* Database files shall be stored in the project file as portable relative paths so that
  projects are shareable across machines.

----

Diagnostic Subsystem (UDS)
--------------------------

**Purpose**: Execute ISO 14229 UDS (Unified Diagnostic Services) commands against an ECU
via ISO-TP transport over CAN.

Requirements:

* The user shall be able to configure a UDS connection by specifying TX and RX arbitration
  IDs, functional address, and timing parameters (P2, P2*).
* The application shall support the following UDS services:

  - DiagnosticSessionControl (0x10) — switch to default, extended, or programming session
  - ECUReset (0x11) — hard, soft, or key-off/on reset
  - ReadDataByIdentifier (0x22) — read one or more DIDs and display the raw and decoded value
  - WriteDataByIdentifier (0x2E) — write a DID with a user-supplied payload
  - SecurityAccess (0x27) — perform a seed-key exchange using a pluggable ``.seedkey.py``
    file that computes the key from the seed
  - TesterPresent (0x3E) — keep the diagnostic session alive
  - ReadDTCInformation (0x19) — read and display Diagnostic Trouble Codes

* All UDS requests shall execute on a background ``UdsWorker`` thread so that the UI
  remains responsive during long-running requests (e.g., flash programming).
* Responses shall be displayed in the Diagnostics panel with service name, success/failure
  status, raw response bytes, and a human-readable interpretation.
* The Watch DID panel shall allow periodic polling of a configurable list of DIDs at a
  user-specified interval (ms).

----

Project Persistence
-------------------

**Purpose**: Save and restore complete session state so users can resume work across
application restarts.

Requirements:

* A project shall be serialised as a single ``.cangui`` JSON file.
* The project file shall include a ``version`` integer field.  The application shall log a
  warning (not crash) when loading a file whose version number exceeds the current
  ``PROJECT_FORMAT_VERSION``.
* The following state shall be persisted:

  - Loaded database file paths
  - Loaded trace file paths and plot trace file paths
  - TX message definitions (ID, data, period, enabled)
  - Watch signal list (arb_id, signal_name, display_name, unit, direction)
  - CAN connection configurations (interface, channel, bitrate, name)
  - Watch DID list (DID, name, cycle_ms)
  - RX filter rules
  - Active script plugin and seed-key plugin file paths
  - Per-project settings overrides
  - Workspace layout (splitter positions, tab order, active tab index)

* File paths shall be stored in portable form (POSIX forward-slash, relative where
  possible using ``walk_up`` traversal).  On load, relative paths shall be resolved
  against the project file directory; missing absolute paths shall fall back to the bare
  filename in the same directory.
* The project shall be marked as modified (dirty) whenever any tracked state changes.
  The main window title shall reflect the modified state with a trailing asterisk.
* The user shall be prompted to save unsaved changes before closing the application or
  opening a different project.

----

Threading Model
---------------

**Purpose**: Keep the UI responsive at all bus speeds by decoupling data acquisition from
rendering.

Requirements:

* Frame reception (``CanReceiver``), frame transmission (``CanTransmitter``), trace disk
  I/O (``DiskWriter``), UDS requests (``UdsWorker``), and trace playback (``TracePlayer``)
  shall each run on a dedicated ``QThread``.
* No worker thread shall ever call Qt widget methods or directly modify Qt model data.
  Cross-thread communication shall use only Qt queued signals or stdlib queues.
* ``CanReceiver`` shall batch received frames: it shall flush to the ``MessageDispatcher``
  after collecting 500 frames or after 20 ms, whichever comes first, to amortise
  signal-emission overhead.
* Qt models that receive batches (``RxMessageModel``, ``TraceModel``) shall accumulate
  frames in a ``_pending`` list on the main thread and drain them via a QTimer (50 ms)
  to decouple bus throughput from display rate.
* A second, longer QTimer (200 ms) shall coalesce ``dataChanged`` emissions so that Qt
  does not repaint every row on every flush.
* ``CanTransmitter`` shall read an immutable snapshot of TX messages rebuilt by the main
  thread; no mutex is required because the snapshot is atomically swapped.
* ``UdsWorker`` shall use a ``queue.Queue`` for request handoff; the worker exits
  automatically when the queue is empty and restarts lazily on the next ``execute()`` call.
