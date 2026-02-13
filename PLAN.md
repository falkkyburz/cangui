# Plan: CAN Network GUI Application (cangui)

## Overview

Professional CAN network GUI application using Python and PySide6 with:
- Rearrangeable docking windows (PySide6-QtAds)
- Tabbed workspaces
- Parameter trees (pyqtgraph) for properties/settings
- Thread-safe CAN communication

## Dependencies (pyproject.toml)

```toml
dependencies = [
    "PySide6>=6.6.0",
    "PySide6-QtAds>=4.5.0",
    "pyqtgraph>=0.13.7",
    "python-can[serial]>=4.4.0",
    "cantools>=39.4.0",
    "odxtools>=8.0.0",           # ODX file parsing
    "udsoncan>=1.25.0",
    "can-isotp>=2.0.0",
    "numpy>=1.26.0",
]
```

## Project Structure

```
cangui/
├── main.py                     # Entry point
├── cangui/
│   ├── __init__.py
│   ├── app.py                  # QApplication setup
│   │
│   ├── core/                   # Business logic (no Qt)
│   │   ├── can_bus.py          # python-can wrapper
│   │   ├── can_message.py      # Message data structures
│   │   ├── database_manager.py # Unified DBC/KCD/ODX loading
│   │   ├── dbc_manager.py      # DBC/KCD loading/decoding (cantools)
│   │   ├── odx_manager.py      # ODX loading/decoding (odxtools)
│   │   ├── signal_decoder.py   # Signal extraction
│   │   ├── uds_client.py       # UDS wrapper
│   │   ├── security_loader.py  # Load external seed-key Python file
│   │   ├── dtc_manager.py      # DTC handling
│   │   ├── trace_writer.py     # ASCII TRC file writer
│   │   ├── trace_reader.py     # TRC file reader for replay
│   │   └── project.py          # Project load/save (all settings)
│   │
│   ├── models/                 # Qt data models
│   │   ├── message_model.py    # CAN message table model
│   │   ├── signal_model.py     # Decoded signals model
│   │   ├── trace_model.py      # Trace log model (circular buffer)
│   │   ├── dtc_model.py        # DTC list model
│   │   ├── watch_model.py      # Watch list model
│   │   └── project_model.py    # File system model
│   │
│   ├── services/               # Bridge core <-> UI (QObjects)
│   │   ├── can_service.py      # CAN connection management
│   │   ├── message_dispatcher.py  # Message routing to subscribers
│   │   ├── uds_service.py      # UDS request/response service
│   │   ├── workspace_service.py   # Layout save/restore
│   │   └── plot_data_service.py   # Time-series buffers
│   │
│   ├── workers/                # Background threads
│   │   ├── can_receiver.py     # CAN receive loop (QThread)
│   │   ├── can_transmitter.py  # Periodic TX (QThread)
│   │   ├── trace_player.py     # Trace replay worker (QThread)
│   │   └── uds_worker.py       # Async UDS (QRunnable)
│   │
│   └── ui/
│       ├── main_window.py      # MainWindow + CDockManager
│       ├── workspace_tabs.py   # Tabbed workspace management
│       │
│       ├── windows/            # Dockable windows
│       │   ├── base_dock_window.py
│       │   ├── project_window.py
│       │   ├── network_window.py
│       │   ├── rx_tx_window.py
│       │   ├── watch_window.py        # CAN signal watch
│       │   ├── watch_did_window.py    # UDS DID watch (periodic)
│       │   ├── trace_window.py
│       │   ├── plot_window.py
│       │   ├── diagnostic_window.py
│       │   └── dtc_window.py
│       │
│       ├── widgets/            # Reusable widgets
│       │   ├── can_interface_selector.py
│       │   ├── message_table.py
│       │   ├── signal_tree.py          # Tree view of DBC signals
│       │   ├── signal_selector.py      # Signal picker for Plot window
│       │   └── hex_editor.py
│       │
│       ├── parameters/         # ParameterTree definitions
│       │   ├── can_settings_params.py
│       │   ├── project_settings_params.py
│       │   ├── rx_message_params.py    # RX message tree with signals
│       │   ├── tx_message_params.py    # TX message tree with enable/signals
│       │   └── signal_params.py
│       │
│       └── dialogs/
│           ├── add_tx_frame_dialog.py  # Add/edit TX frame
│           ├── import_dbc_dialog.py    # DBC/ODX import dialog
│           └── uds_request_dialog.py
```

## Main Window Menu Bar

**File Menu:**
- New Project, Open Project, Save Project, Recent Projects, Exit

**View Menu** (button for each window type):
- Project Window
- Network Window
- Receive/Transmit Window
- Watch Window (CAN Signals)
- Watch DID Window (UDS DIDs)
- Trace Window
- Plot Window
- Diagnostic Window
- DTC Window

**Tools Menu:**
- Import DBC/KCD/ODX...
- Start Virtual CAN (vcan setup)
- Settings

## Architecture Pattern

**Service-Oriented MVP** with Qt signals/slots:

```
UI Layer (Views/Windows)
    ↓ Qt Signals/Slots
Services Layer (QObjects - main thread)
    ↓ Thread-safe queues
Workers Layer (QThread/QRunnable)
    ↓
Core Layer (No Qt dependencies)
```

## Subsystems

### 1. Project Window
- File browser (QTreeView + QFileSystemModel)
- **Import database files** via menu or drag-drop:
  - DBC (Vector format)
  - KCD (open source XML format, supported by cantools)
  - ODX (diagnostic format)
- **Project Settings ParameterTree** containing ALL application settings:
  - CAN interface settings (type, channel, bitrate, CAN FD)
  - Window layout preferences
  - Plot settings (colors, time range)
  - Trace settings (buffer size, filters)
  - Diagnostic settings (UDS addressing)
- Project saved as single .json file with all settings + references to DBC/ODX files

### 2. Network Window
- CAN interface type selector (socketcan, vector, peak, kvaser, virtual)
- Channel, bitrate, CAN FD settings via ParameterTree
- Connect/Disconnect actions
- **Virtual CAN support** (Linux vcan for testing without hardware):
  ```bash
  # Setup vcan interface (run once)
  sudo modprobe vcan
  sudo ip link add dev vcan0 type vcan
  sudo ip link set up vcan0
  ```
- Auto-detect available interfaces

### 3. Receive/Transmit Window
Both RX and TX use ParameterTree with expandable message groups:

**RX ParameterTree columns:**
- Last Receive Time
- Cycle Time (ms)
- CAN ID (hex)
- DLC
- Frame Type (CAN/CAN-FD/Error)

**RX structure (expandable):**
```
├─ 0x123 - EngineData    [12:34:56.789] [100ms] [8] [CAN]
│   ├─ Data: 01 02 03 04 05 06 07 08
│   ├─ RPM: 3500 rpm
│   ├─ Throttle: 45.2%
│   └─ Temperature: 92°C
├─ 0x456 - BrakeStatus   [12:34:56.812] [50ms] [8] [CAN]
│   └─ ...
├─ ERROR FRAME           [12:34:57.001] [--] [0] [Error]
│   └─ Error Type: BUS_ERROR
```

**CAN Error Frame Support:**
- Display error frames in RX list with distinct styling (red)
- Show error type (BUS_ERROR, ERROR_PASSIVE, etc.)

**TX ParameterTree:**
- Enabled (checkbox to enable/disable transmission)
- CAN ID, Frame Type, Cycle Time (editable)
- Data field (expandable with decoded signals for editing)

**TX Actions (toolbar/context menu):**
- **Add Frame**: Add new TX message (from DBC or manual)
- **Edit Frame**: Modify CAN ID, name, signals
- **Remove Frame**: Delete TX message from list
- **Import from DBC**: Select messages from loaded DBC to add

**TX structure (expandable):**
```
├─ [✓] 0x100 - TestMessage  [Cycle: 100ms] [DLC: 8]
│   ├─ Data: 00 00 00 00 00 00 00 00
│   ├─ Signal1: 0 (editable)
│   └─ Signal2: 0 (editable)
├─ [ ] 0x200 - CustomFrame  [Cycle: 50ms] [DLC: 8]
│   └─ Data: FF FF FF FF FF FF FF FF
```

### 4. Watch Window (CAN Signals)
- Watch decoded signal values from CAN messages
- Drag signals from RX window or DBC browser
- Display format config (decimal, hex, engineering units)
- Real-time updates as messages arrive
- **Signal list persisted in project file**

### 5. Watch DID Window (UDS DIDs)
- Watch UDS DID values (separate from CAN signals)
- **Configurable cycle time** for periodic DID reads (e.g., 100ms, 500ms, 1s)
- Add DIDs by ID or from ODX database
- Display format config (decimal, hex, raw bytes)
- Start/Stop polling button
- **DID list persisted in project file**

### 6. Trace Window
- High-performance trace with circular buffer (100k messages)
- Columns: Timestamp, ID, Dir, DLC, Data, Decoded
- Filtering, search, batch updates (50ms)
- **Toolbar**: Start, Pause, Stop buttons
- **Trace storage**: Save to `traces/` folder next to project file
- **ASCII TRC format** (PEAK compatible):
  ```
  ;$FILEVERSION=1.1
  ;   Start time: 01/15/2025 14:30:00.000
  ;-------------------------------------------------------------------------------
  ;   Message Number) Time Offset   Type   ID    Rx/Tx   d]  Data Bytes ...
  ;-------------------------------------------------------------------------------
        1)      0.000 1  0123 Rx  d 8  01 02 03 04 05 06 07 08
        2)      0.100 1  0456 Rx  d 8  AA BB CC DD EE FF 00 11
  ```
- **Trace Replay**:
  - Load TRC files for replay
  - Playback controls: Play, Pause, Stop, Speed (0.5x, 1x, 2x, 10x, Max)
  - Replayed messages feed into Watch, Plot, and RX windows
  - Timeline scrubber to jump to specific time
  - Allows plotting signals from captured traces offline

### 7. Plot Window
- pyqtgraph PlotWidget for time-series
- Rolling data buffers managed by PlotDataService
- **Signal Selector**: Tree view of all signals from loaded DBC/ODX files
  - Browse by Message → Signal hierarchy
  - Search/filter signals by name
  - Drag signals to plot or use Add button
- Multiple Y-axes support for different signal ranges
- Configurable colors per signal
- Time range selection (rolling window, fixed range)

### 8. Diagnostic Window
- UDS service selector (0x10, 0x22, 0x2E, 0x27, 0x31, etc.)
- DID configuration ParameterTree
- Request/Response display
- **Security Access (0x27)**: Uses external Python file for seed-key algorithm
  - **Seedkey file path configured in Project Settings** (UDS Settings → Security Algorithm File)
  ```python
  # Example: my_security.py
  def calculate_key(seed: bytes, security_level: int) -> bytes:
      """Calculate key from seed for security access"""
      # Custom algorithm here
      return key_bytes
  ```

### 9. DTC Window
- Read DTCs (stored/pending/permanent)
- Clear DTCs
- DTC status decoding, freeze frame

## Key Patterns

### Base Dock Window
```python
class BaseDockWindow(ads.CDockWidget):
    def __init__(self, title: str):
        super().__init__(title)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self.setWidget(self._content)
```

### CAN Service with Worker Thread
```python
class CANService(QObject):
    message_received = Signal(object)

    def connect(self, config):
        self._bus = can.Bus(**config)
        self._worker = CANReceiverWorker(self._bus)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.message_received.connect(self.message_received)
```

### Message Dispatcher
```python
class MessageDispatcher(QObject):
    def subscribe(self, arb_id: int, callback): ...
    def subscribe_all(self, callback): ...  # For trace
    def dispatch(self, message): ...
```

### RX/TX ParameterTree Message
```python
def create_rx_message_param(can_id: int, name: str, signals: list) -> dict:
    """Create expandable RX message parameter group"""
    children = [
        {'name': 'Data', 'type': 'str', 'value': '00 00 00 00 00 00 00 00', 'readonly': True}
    ]
    for sig in signals:
        children.append({
            'name': sig.name,
            'type': 'float',
            'value': 0.0,
            'suffix': sig.unit,
            'readonly': True
        })

    return {
        'name': f'0x{can_id:03X} - {name}',
        'type': 'group',
        'children': children,
        # Custom columns: Last RX Time, Cycle Time, Frame Type
        'last_rx': '--:--:--.---',
        'cycle_time': '-- ms',
        'frame_type': 'CAN',
    }

def create_tx_message_param(can_id: int, name: str, signals: list) -> dict:
    """Create expandable TX message parameter group with enable"""
    children = [
        {'name': 'Enabled', 'type': 'bool', 'value': False},
        {'name': 'Cycle Time', 'type': 'int', 'value': 100, 'suffix': 'ms'},
        {'name': 'Data', 'type': 'str', 'value': '00 00 00 00 00 00 00 00'}
    ]
    for sig in signals:
        children.append({
            'name': sig.name,
            'type': 'float',
            'value': 0.0,
            'suffix': sig.unit,
            'readonly': False  # Editable for TX
        })

    return {
        'name': f'0x{can_id:03X} - {name}',
        'type': 'group',
        'children': children,
    }
```

### Project Settings (All App Settings)
```python
PROJECT_SETTINGS_PARAMS = [
    {'name': 'Database Files', 'type': 'group', 'children': [
        {'name': 'DBC/KCD Files', 'type': 'list', 'values': []},
        {'name': 'ODX Files', 'type': 'list', 'values': []},
    ]},
    {'name': 'CAN Interface', 'type': 'group', 'children': [
        {'name': 'Type', 'type': 'list', 'values': ['socketcan', 'vector', 'peak', 'kvaser', 'virtual']},
        {'name': 'Channel', 'type': 'str', 'value': 'vcan0'},
        {'name': 'Bitrate', 'type': 'list', 'values': [125000, 250000, 500000, 1000000]},
        {'name': 'CAN FD Enabled', 'type': 'bool', 'value': False},
        {'name': 'Data Bitrate', 'type': 'list', 'values': [1000000, 2000000, 5000000]},
    ]},
    {'name': 'Trace Settings', 'type': 'group', 'children': [
        {'name': 'Buffer Size', 'type': 'int', 'value': 100000},
        {'name': 'Auto Scroll', 'type': 'bool', 'value': True},
        {'name': 'Trace Folder', 'type': 'str', 'value': 'traces/'},
    ]},
    {'name': 'Plot Settings', 'type': 'group', 'children': [
        {'name': 'Time Window', 'type': 'float', 'value': 10.0, 'suffix': 's'},
        {'name': 'Update Rate', 'type': 'int', 'value': 50, 'suffix': 'ms'},
    ]},
    {'name': 'UDS Settings', 'type': 'group', 'children': [
        {'name': 'TX ID', 'type': 'str', 'value': '0x7E0'},
        {'name': 'RX ID', 'type': 'str', 'value': '0x7E8'},
        {'name': 'Timeout', 'type': 'float', 'value': 2.0, 'suffix': 's'},
        {'name': 'Security Algorithm File', 'type': 'file', 'value': ''},  # Seedkey .py file
    ]},
    {'name': 'Watch DID Settings', 'type': 'group', 'children': [
        {'name': 'Default Cycle Time', 'type': 'int', 'value': 500, 'suffix': 'ms'},
    ]},
]

# Project file also stores (in JSON, not ParameterTree):
# - watch_signals: List of watched CAN signal paths (e.g., ["EngineData.RPM", "BrakeStatus.BrakePedal"])
# - watch_dids: List of watched DIDs with config (e.g., [{"did": 0xF190, "name": "VIN", "cycle_ms": 1000}])
# - tx_messages: List of configured TX messages
```

## Implementation Order (Iterative)

Build incrementally - each phase delivers working functionality.

### Phase 1: Basic Receive/Transmit (MVP)
**Goal**: Connect to CAN, see messages, send messages

1. Project structure + `pyproject.toml`
2. `main.py` + `app.py` - Application entry
3. `main_window.py` - Basic window with CDockManager
4. `base_dock_window.py` - Base class for dock windows
5. `core/can_bus.py`, `core/can_message.py` - CAN abstractions
6. `workers/can_receiver.py` - Receive thread
7. `services/can_service.py`, `services/message_dispatcher.py`
8. `network_window.py` - Interface selection (vcan support)
9. `rx_tx_window.py` - Basic RX list + manual TX
10. `workers/can_transmitter.py` - Periodic TX

**Deliverable**: App that connects to vcan0, displays received messages, sends messages

### Phase 2: DBC Decoding + Watch Window
**Goal**: Load DBC, decode signals, watch values

11. `core/database_manager.py` - Unified DBC/KCD loader
12. `core/dbc_manager.py` - DBC/KCD parsing (cantools)
13. `core/signal_decoder.py` - Signal extraction
14. Update `rx_tx_window.py` - Expandable decoded signals
15. `watch_window.py` + `watch_model.py` - CAN signal watch
16. `project_window.py` + `project.py` - Project settings, DBC import

**Deliverable**: Load DBC, see decoded signals in RX, watch specific signals

### Phase 3: Trace + Plot
**Goal**: Record traces, plot signals

17. `core/trace_writer.py` - ASCII TRC writer
18. `core/trace_reader.py` - TRC reader for replay
19. `trace_window.py` + `trace_model.py` - Start/pause/stop, save TRC
20. `workers/trace_player.py` - Trace replay
21. `ui/widgets/signal_selector.py` - DBC signal browser
22. `plot_window.py` + `plot_data_service.py` - Signal plotting

**Deliverable**: Record TRC traces, replay them, plot signals over time

### Phase 4: UDS Diagnostics
**Goal**: Basic UDS communication

23. `core/odx_manager.py` - ODX parsing (odxtools)
24. `core/uds_client.py` - UDS client wrapper
25. `workers/uds_worker.py` - Async UDS requests
26. `services/uds_service.py` - UDS service layer
27. `diagnostic_window.py` - Basic UDS services (0x10, 0x22, 0x2E)

**Deliverable**: Send basic UDS requests, read/write DIDs

### Phase 5: Advanced Diagnostics
**Goal**: Security access, DID watch, DTCs

28. `core/security_loader.py` - Load seedkey Python file
29. Update `diagnostic_window.py` - Security access (0x27)
30. `watch_did_window.py` - Periodic DID polling
31. `core/dtc_manager.py` - DTC handling
32. `dtc_window.py` - Read/clear DTCs

**Deliverable**: Unlock ECU with custom seedkey, watch DIDs periodically, manage DTCs

### Phase 6: Polish + Persistence
**Goal**: Full project save/load, complete UI

33. Complete `project.py` - Save all settings, watch lists, TX messages
34. `workspace_tabs.py` + `workspace_service.py` - Layout persistence
35. `ui/dialogs/add_tx_frame_dialog.py` - Add/edit/remove TX frames
36. Menu bar - View menu with all windows, Tools menu
37. Error frame display in RX window

**Deliverable**: Complete application with full persistence

## Verification

After each phase:
1. Run `python main.py` - App should launch without errors
2. Test docking - Drag windows, create tabs, restore layouts
3. Test CAN (Phase 2+):
   - Setup vcan: `sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set up vcan0`
   - Connect to vcan0, use `cansend vcan0 123#DEADBEEF` to test RX
4. Test decoding (Phase 3+) - Load example.dbc or .kcd, verify signals decode
5. Test trace (Phase 4+) - Start/pause/stop, verify TRC file in traces/ folder
6. Test UDS (Phase 5+) - Test security access with linked Python file

## Critical Files

- `cangui/ui/main_window.py` - Main window with menu bar and docking
- `cangui/services/can_service.py` - Central CAN message hub
- `cangui/core/database_manager.py` - Unified DBC/KCD/ODX loading
- `cangui/core/trace_writer.py` - ASCII TRC file writer
- `cangui/core/security_loader.py` - Load external seed-key algorithm
- `cangui/ui/windows/base_dock_window.py` - Pattern for all windows
- `cangui/ui/windows/watch_did_window.py` - Periodic DID polling
- `cangui/workers/can_receiver.py` - Thread-safe receive loop
