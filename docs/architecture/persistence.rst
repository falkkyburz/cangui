Project Persistence
===================

Two-tier settings
-----------------

cangui separates settings into two tiers:

**Global options** — ``~/.config/cangui/options.json``
  Persisted by ``AppOptions.save()`` / ``AppOptions.load()``.  Contains
  settings that are meaningful across all projects: plot time window,
  update interval, trace format, tab visibility flags.  These survive
  project switches and machine restarts.

**Per-project settings** — embedded in the project JSON under ``settings``
  Overrides to global options that travel with the project file.
  Restored by ``SettingsWindow.apply_project_settings()`` when a project
  is opened.  Collected by ``SettingsWindow.collect_settings()`` when the
  project is saved.


ProjectData field inventory
----------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Field
     - Content
     - Serialized as
   * - ``name``
     - Project name (derived from filename stem on load)
     - String
   * - ``version``
     - Project format version (see below)
     - Integer
   * - ``database_files``
     - Paths to DBC/KCD/ODX files
     - Portable paths (see below)
   * - ``trace_files``
     - Paths to trace recordings (internal ``.ctb`` plus imported/exported TRC/BLF)
     - Portable paths
   * - ``plot_files``
     - Paths to plot trace recordings
     - Portable paths
   * - ``watch_signals``
     - List of ``{arb_id, signal_name, display_name, unit, direction}``
     - JSON array
   * - ``tx_messages``
     - TX message configurations including raw_data (hex string)
     - JSON array
   * - ``connections``
     - CAN interface configurations
     - JSON array
   * - ``watch_dids``
     - DID polling entries ``{did, name, cycle_ms}``
     - JSON array
   * - ``rx_filters``
     - RX filter rules
     - JSON array
   * - ``workspace_state``
     - Serialized splitter sizes and tab layout (JSON-in-JSON)
     - String (JSON)
   * - ``settings``
     - Per-project settings overrides
     - JSON object
   * - ``script_plugin_file``
     - Path to ``.script.py`` plugin (or ``""`` if none)
     - Portable path
   * - ``seedkey_file``
     - Path to ``.seedkey.py`` plugin (or ``""`` if none)
     - Portable path
   * - ``database_editor_file``
     - Basename of the separate ``.db.json`` file (or ``""``)
     - String (basename only)


Path portability strategy
--------------------------

File paths are the trickiest data in a project file because:

* The project may be shared across machines with different home
  directories.
* The project folder may be moved or renamed after it is created.
* Windows drive letters make absolute paths non-portable across platforms.

**On save** — ``Project._make_portable(path)``

Tries to produce a *relative* path from the project file's directory to
the target file using ``Path.relative_to(..., walk_up=True)``.  Walk-up
allows ``../../sibling/file.dbc`` style paths so that files adjacent to
the project directory can be stored portably.  Falls back to an absolute
POSIX path only when a relative path is impossible (e.g., different
Windows drives).  Forward slashes are always used regardless of the
current platform.

**On load** — ``Project._resolve_stored_path(stored)``

Resolution order:

1. **Relative** — resolved against the project file's parent directory.
2. **Absolute, exists** — used as-is.
3. **Absolute, missing** — tries ``<project_dir>/<basename>`` as a
   last-resort fallback for moved or cross-platform projects.
4. **Give up** — returns the stored string unchanged so the UI can show
   "File not found" instead of crashing.


Workspace state
---------------

The ``workspace_state`` field stores a JSON object (as a string inside
the outer JSON):

.. code-block:: json

    {
      "h_splitter":    [800, 400],
      "v_splitter":    [200, 600],
      "rxtx_splitter": [300, 200, 100],
      "main_tabs":     {"order": ["Receive/Transmit [1]", "…"], "current": 0},
      "small_tabs":    {"order": ["Project Manager [5]", "Log [L]"], "current": 0},
      "list_tabs":     {"order": ["Watch [6]", "…"], "current": 0}
    }

Splitter values are pixel sizes.  Tab ``order`` is a list of tab label
strings; ``current`` is the active tab index.  ``WorkspaceService``
restores tab order by moving tabs on the tab bar without recreating the
widgets.


Project format versioning
--------------------------

The ``version`` field was introduced in format version 1.  Files without
this field are treated as version 0 (pre-versioning) and loaded with
best-effort compatibility via ``raw.get("field", default)`` calls.

The constant ``PROJECT_FORMAT_VERSION`` in ``project.py`` holds the
current writer version.  ``Project.load()`` logs a ``WARNING`` (does not
crash) if the file's version is higher than the current build supports,
so users with newer projects can still open them in older builds with a
diagnostic message.


The separate ``.db.json`` file
--------------------------------

The Database editor stores *manually created* messages and signals
(not imported from DBC/ODX) in a sibling file next to the project:

.. code-block:: text

    my_project.json
    my_project.db.json   ← database editor content

``project.save_database_editor(data)`` writes this file and records its
basename in ``ProjectData.database_editor_file``.
``project.load_database_editor()`` reads it back.  The separation keeps
the main project file readable and prevents the editor data (which can
be large) from bloating it.


Backwards compatibility
-----------------------

The loader uses ``raw.get("field", default)`` everywhere, so new fields
added to ``ProjectData`` are simply absent when loading old files.
Removed fields are silently ignored by the JSON loader.  Old aliases are
resolved explicitly:

* ``rxtx_plugin_file`` → ``script_plugin_file``
* ``e2e_plugin_file``  → ``script_plugin_file``
