"""Central icon registry using qtawesome (Font Awesome 5)."""
import qtawesome as qta
from PySide6.QtGui import QIcon

_C  = "#3C3C3E"   # neutral / default
_RD = "#C62828"   # red — destructive or record
_GN = "#2E7D32"   # green — start / connect
_AM = "#E67E00"   # amber — warning / move
_BL = "#1565C0"   # blue — navigation / info
_PU = "#6A1B9A"   # purple — watch / monitor
_TE = "#00695C"   # teal — signals / plot
_OR = "#E65100"   # orange — clear / erase

_MAP: dict[str, tuple[str, str]] = {
    # CRUD
    "add":          ("fa5s.plus",           _GN),
    "remove":       ("fa5s.minus",          _RD),
    "trash":        ("fa5s.trash-alt",      _RD),
    "copy":         ("fa5s.copy",           _BL),
    # File
    "new":          ("fa5s.file",           _BL),
    "open":         ("fa5s.folder-open",    _AM),
    "save":         ("fa5s.save",           _BL),
    "save-as":      ("fa5s.file-export",    _BL),
    "import":       ("fa5s.file-import",    _BL),
    "export":       ("fa5s.file-export",    _BL),
    # Transport
    "send":         ("fa5s.paper-plane",    _GN),
    "up":           ("fa5s.arrow-up",       _BL),
    "down":         ("fa5s.arrow-down",     _BL),
    "refresh":      ("fa5s.sync-alt",       _AM),
    # Playback
    "play":         ("fa5s.play",           _GN),
    "pause":        ("fa5s.pause",          _AM),
    "stop":         ("fa5s.stop",           _RD),
    "record":       ("fa5s.circle",         _RD),
    # Views
    "watch":        ("fa5s.eye",            _PU),
    "plot":         ("fa5s.chart-line",     _TE),
    "search":       ("fa5s.search",         _BL),
    "clear":        ("fa5s.eraser",         _OR),
    "broom":        ("fa5s.broom",          _OR),
    # Network
    "connect":      ("fa5s.plug",           _GN),
    "disconnect":   ("fa5s.unlink",         _RD),
    "heartbeat":    ("fa5s.heartbeat",      _RD),
    # Database / CAN
    "database":     ("fa5s.database",       _BL),
    "message":      ("fa5s.comment-alt",    _BL),
    "signal":       ("fa5s.wave-square",    _TE),
    # Filters
    "pass":         ("fa5s.check-circle",   _GN),
    "drop":         ("fa5s.times-circle",   _RD),
    # Status indicators
    "status-ok":       ("fa5s.circle",      _GN),
    "status-warn":     ("fa5s.circle",      _AM),
    "status-error":    ("fa5s.circle",      _RD),
    "status-off":      ("fa5s.circle",      _C),
}


def icon(name: str) -> QIcon:
    """Return a QIcon for the given logical name, or an empty QIcon if unknown."""
    entry = _MAP.get(name)
    if entry is None:
        return QIcon()
    icon_name, color = entry
    try:
        return qta.icon(icon_name, color=color)
    except Exception:
        return QIcon()
