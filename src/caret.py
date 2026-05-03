"""
AT-SPI tracker for contextual popup anchoring.

Records the active window, focused text object, and caret rectangle when apps
expose them. Many Linux apps expose incomplete accessibility geometry, so the
public anchor API reports a confidence/source instead of pretending every
coordinate is equally trustworthy.
"""
import time
from typing import Optional, TypedDict

_atspi = None
_listener = None
_win_listener = None

from gi.repository import Gio, GLib

# Active window bounds (updated on window:activate or via Introspect)
_win_x: int = 0
_win_y: int = 0
_win_w: int = 0
_win_h: int = 0
_win_app: str = ''
_win_ts: float = 0.0

# Focused text object bounds (updated on focus and caret events)
_focus_x: int = 0
_focus_y: int = 0
_focus_w: int = 0
_focus_h: int = 0
_focus_ts: float = 0.0
_focus_app: str = ''
_focus_role: str = ''
_focus_name: str = ''
_focus_description: str = ''

# Caret bounds (updated on object:text-caret-moved)
_caret_x: int = 0
_caret_y: int = 0
_caret_w: int = 0
_caret_h: int = 0
_caret_ts: float = 0.0
_caret_app: str = ''

# IBus caret tracking (often absolute screen coords on Wayland)
_ibus_x: int = 0
_ibus_y: int = 0
_ibus_w: int = 0
_ibus_h: int = 0
_ibus_ts: float = 0.0

_last_scan_ts: float = 0.0

# DBus proxies
_ibus_proxy = None
_introspect_proxy = None


class Anchor(TypedDict):
    source: str
    confidence: str
    x: int
    y: int
    width: int
    height: int
    age_ms: int
    app: str


def start() -> bool:
    print('[caret] start() called', flush=True)
    global _atspi, _listener, _win_listener

    # Start IBus listener
    _start_ibus_listener()

    # Start GNOME Introspect proxy
    _start_introspect_proxy()

    try:
        import subprocess
        r = subprocess.run(
            ['gdbus', 'call', '--session',
             '--dest', 'org.a11y.Bus',
             '--object-path', '/org/a11y/bus',
             '--method', 'org.a11y.Bus.GetAddress'],
            capture_output=True, timeout=0.5
        )
        if r.returncode != 0:
            return False
    except Exception:
        return False

    try:
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi

        try:
            result = Atspi.init()
            print(f'[caret] Atspi.init() returned {result}', flush=True)
        except Exception as e:
            print(f'[caret] Atspi.init() failed: {e}', flush=True)
            return False

        _atspi = Atspi

        _listener = Atspi.EventListener.new(_on_caret_event, None)
        ok1 = _listener.register('object:text-caret-moved')
        ok2 = _listener.register('focus:')

        _win_listener = Atspi.EventListener.new(_on_window_event, None)
        ok3 = _win_listener.register('window:activate')

        print(f'[caret] listeners: caret={ok1}, focus={ok2}, window={ok3}', flush=True)
        return ok1 or ok2 or ok3
    except Exception:
        return False


def _start_ibus_listener():
    """Listen for IBus SetCursorLocation signals."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.signal_subscribe(
            None,  # sender
            'org.freedesktop.IBus.Panel',
            'SetCursorLocation',
            None,  # object path
            None,  # arg0
            Gio.DBusSignalFlags.NONE,
            _on_ibus_signal,
            None
        )
        print('[caret] IBus signal subscription active', flush=True)
    except Exception as e:
        print(f'[caret] IBus subscription failed: {e}', flush=True)


def _on_ibus_signal(connection, sender_name, object_path, interface_name, signal_name, parameters, user_data):
    global _ibus_x, _ibus_y, _ibus_w, _ibus_h, _ibus_ts
    try:
        # parameters is a GLib.Variant (x, y, w, h)
        x, y, w, h = parameters.unpack()
        # IBus coords are often absolute screen coords on Wayland GNOME.
        if x != 0 or y != 0:
            _ibus_x, _ibus_y, _ibus_w, _ibus_h = x, y, w, h
            _ibus_ts = time.monotonic()
    except Exception:
        pass


def _start_introspect_proxy():
    global _introspect_proxy
    try:
        _introspect_proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            'org.gnome.Shell.Introspect',
            '/org/gnome/Shell/Introspect',
            'org.gnome.Shell.Introspect',
            None
        )
    except Exception as e:
        print(f'[caret] Introspect proxy failed: {e}', flush=True)


def _update_window_from_introspect():
    """Query GNOME Shell for the active window's absolute position."""
    global _win_x, _win_y, _win_w, _win_h, _win_app, _win_ts
    if not _introspect_proxy:
        return

    try:
        # GetWindows returns a dict {id: {title, wm_class, ...}}
        # But we actually want the focus state.
        # Introspect doesn't have a direct "GetActiveWindow" but we can look for
        # the window that has is-active=true.
        # Wait, Introspect's GetWindows returns (a{sv}a{sv}) usually?
        # Let's check the actual signature. It's often:
        # GetWindows() -> (a{sv} windows)
        result = _introspect_proxy.call_sync(
            'GetWindows',
            None,
            Gio.DBusCallFlags.NONE,
            100,
            None
        )
        if not result:
            return

        windows = result.unpack()[0]
        for win_id, props in windows.items():
            if props.get('has-focus') or props.get('is-active'):
                _win_x = int(props.get('x', _win_x))
                _win_y = int(props.get('y', _win_y))
                _win_w = int(props.get('width', _win_w))
                _win_h = int(props.get('height', _win_h))
                _win_app = str(props.get('wm-class', _win_app)).lower()
                _win_ts = time.monotonic()
                break
    except Exception:
        pass


def _on_window_event(event, _user_data) -> None:
    global _win_x, _win_y, _win_w, _win_h, _win_app, _win_ts
    try:
        obj = event.source
        if obj is None:
            return
        extents = obj.get_extents(_atspi.CoordType.SCREEN)
        
        # On Wayland, if we get (0,0), try Introspect
        if extents.x == 0 and extents.y == 0:
            _update_window_from_introspect()
            return

        # Ignore dropdowns, tooltips, and context menus — they're too small
        # to be the main application window and would corrupt our position cache.
        if extents.width < 400 or extents.height < 200:
            return
        _win_x = extents.x
        _win_y = extents.y
        _win_w = extents.width
        _win_h = extents.height
        _win_ts = time.monotonic()
        try:
            app = obj.get_application()
            _win_app = (app.get_name() or '').lower() if app else ''
        except Exception:
            _win_app = ''
        print(f'[caret] window:activate {_win_app!r} → {_win_x},{_win_y} {_win_w}x{_win_h}', flush=True)
    except Exception as e:
        print(f'[caret] window event error: {e}', flush=True)


def _on_caret_event(event, _user_data) -> None:
    global _caret_x, _caret_y, _caret_w, _caret_h, _caret_ts
    global _focus_x, _focus_y, _focus_w, _focus_h, _focus_ts, _win_app
    global _caret_app, _focus_app

    try:
        obj = event.source
        if obj is None:
            return
        _record_focus_metadata(obj)
        text = obj.get_text_iface()
        if text is None:
            return

        try:
            app_obj = obj.get_application()
            name = (app_obj.get_name() or '').lower() if app_obj else ''
            if name:
                _win_app = name
        except Exception:
            pass

        focused = _safe_extents(obj)
        if focused:
            _focus_x, _focus_y, _focus_w, _focus_h = focused
            _focus_ts = time.monotonic()
            _focus_app = _win_app

        caret = _safe_caret_extents(text)
        if caret:
            _caret_x, _caret_y, _caret_w, _caret_h = caret
            _caret_ts = time.monotonic()
            _caret_app = _win_app
    except Exception:
        pass


def _refresh_from_desktop() -> None:
    """Query the currently focused accessible on demand.

    Some apps do not emit useful focus/caret events to a long-running listener
    after session restarts, but their focused object can still be found by
    walking the AT-SPI tree when the popup is requested.
    """
    global _last_scan_ts
    if _atspi is None:
        return
    now = time.monotonic()
    if now - _last_scan_ts < 0.25:
        return
    _last_scan_ts = now

    deadline = now + 0.12
    try:
        desktop_count = _atspi.get_desktop_count()
    except Exception:
        desktop_count = 1

    for i in range(max(1, desktop_count)):
        try:
            desktop = _atspi.get_desktop(i)
        except Exception:
            continue
        found = _find_focused_accessible(desktop, deadline, [0], [None])
        if found is not None:
            _record_focus_object(found, 'scan')
            return


def _find_focused_accessible(obj, deadline: float, visited: list[int],
                             fallback: list):
    if obj is None or time.monotonic() > deadline or visited[0] > 900:
        return fallback[0]
    visited[0] += 1

    try:
        states = obj.get_state_set()
        if states and states.contains(_atspi.StateType.FOCUSED):
            if _has_text_iface(obj):
                return obj
            if fallback[0] is None:
                fallback[0] = obj
    except Exception:
        pass

    try:
        child_count = obj.get_child_count()
    except Exception:
        return None

    for idx in range(child_count):
        try:
            child = obj.get_child_at_index(idx)
        except Exception:
            continue
        found = _find_focused_accessible(child, deadline, visited, fallback)
        if found is not None:
            try:
                if _has_text_iface(found):
                    return found
            except Exception:
                pass
    return fallback[0]


def _has_text_iface(obj) -> bool:
    try:
        return obj.get_text_iface() is not None
    except Exception:
        return False


def _record_focus_object(obj, reason: str) -> None:
    global _caret_x, _caret_y, _caret_w, _caret_h, _caret_ts
    global _focus_x, _focus_y, _focus_w, _focus_h, _focus_ts, _win_app
    global _caret_app, _focus_app

    try:
        app_obj = obj.get_application()
        name = (app_obj.get_name() or '').lower() if app_obj else ''
        if name:
            _win_app = name
    except Exception:
        pass
    _record_focus_metadata(obj)

    focused = _safe_extents(obj)
    if focused:
        _focus_x, _focus_y, _focus_w, _focus_h = focused
        _focus_ts = time.monotonic()
        _focus_app = _win_app

    try:
        text = obj.get_text_iface()
    except Exception:
        text = None
    if text is None:
        return

    caret = _safe_caret_extents(text)
    if caret:
        _caret_x, _caret_y, _caret_w, _caret_h = caret
        _caret_ts = time.monotonic()
        _caret_app = _win_app
        print(
            f'[caret] {reason}: caret {_win_app!r} '
            f'→ {_caret_x},{_caret_y} {_caret_w}x{_caret_h}',
            flush=True,
        )
    elif focused:
        print(
            f'[caret] {reason}: focused-text {_win_app!r} '
            f'→ {_focus_x},{_focus_y} {_focus_w}x{_focus_h}',
            flush=True,
        )


def _record_focus_metadata(obj) -> None:
    global _focus_role, _focus_name, _focus_description

    try:
        role = obj.get_role_name() or ''
    except Exception:
        role = ''
    try:
        name = obj.get_name() or ''
    except Exception:
        name = ''
    try:
        description = obj.get_description() or ''
    except Exception:
        description = ''

    _focus_role = role.lower()
    _focus_name = name.lower()
    _focus_description = description.lower()


def _safe_extents(obj) -> Optional[tuple[int, int, int, int]]:
    try:
        extents = obj.get_extents(_atspi.CoordType.SCREEN)
        if extents.width <= 0 or extents.height <= 0:
            return None
        return (extents.x, extents.y, extents.width, extents.height)
    except Exception:
        return None


def _safe_caret_extents(text) -> Optional[tuple[int, int, int, int]]:
    try:
        offset = text.get_caret_offset()
        for candidate in (offset, offset - 1):
            if candidate < 0:
                continue
            extents = text.get_character_extents(candidate, _atspi.CoordType.SCREEN)
            width = max(2, extents.width)
            height = max(12, extents.height)
            if extents.x == 0 and extents.y == 0:
                continue
            if extents.x < -10000 or extents.y < -10000:
                continue
            return (extents.x, extents.y, width, height)
    except Exception:
        return None
    return None


def _inside_window(x: int, y: int, w: int, h: int) -> bool:
    if _win_w <= 0 or _win_h <= 0:
        return True
    if time.monotonic() - _win_ts > 30.0:
        return True
    cx = x + max(1, w) // 2
    cy = y + max(1, h) // 2
    return _win_x <= cx <= _win_x + _win_w and _win_y <= cy <= _win_y + _win_h


def _same_app(anchor_app: str) -> bool:
    return not anchor_app or not _win_app or anchor_app == _win_app


def _anchor(source: str, confidence: str, rect: tuple[int, int, int, int],
            ts: float) -> Anchor:
    x, y, w, h = rect
    age_ms = int(max(0.0, time.monotonic() - ts) * 1000)
    return {
        'source': source,
        'confidence': confidence,
        'x': x,
        'y': y,
        'width': w,
        'height': h,
        'age_ms': age_ms,
        'app': _win_app,
    }


def get_app_name() -> str:
    return _win_app


def get_focus_hints() -> dict[str, str]:
    """Return metadata about the currently focused accessible object."""
    _refresh_from_desktop()
    return {
        'app': _win_app,
        'role': _focus_role,
        'name': _focus_name,
        'description': _focus_description,
    }


def get_window_geometry() -> Optional[tuple[int, int, int, int]]:
    """Return (x, y, width, height) of the last activated window, or None."""
    _update_window_from_introspect()
    if _win_w > 0 and _win_h > 0:
        return (_win_x, _win_y, _win_w, _win_h)
    return None


def get_anchor() -> Optional[Anchor]:
    """Return the best current popup anchor with confidence metadata."""
    _refresh_from_desktop()
    _update_window_from_introspect()
    now = time.monotonic()

    # 1. Prefer IBus absolute coords if fresh
    if _ibus_ts > 0 and now - _ibus_ts < 2.0:
        return _anchor('ibus', 'high', (_ibus_x, _ibus_y, _ibus_w, _ibus_h), _ibus_ts)

    # 2. Prefer AT-SPI caret
    caret_rect = (_caret_x, _caret_y, _caret_w, _caret_h)
    
    # On Wayland, if caret is relative, fuse with window position
    if _caret_w > 0 and _caret_h > 0 and now - _caret_ts < 8.0:
        cx, cy, cw, ch = caret_rect
        # If coords are small or relative to window (heuristically detected)
        # On Wayland GNOME, relative coords are common.
        if (cx == 0 and cy == 0) or (cx < 2000 and cy < 2000 and not _inside_window(*caret_rect)):
            if _win_w > 0:
                caret_rect = (cx + _win_x, cy + _win_y, cw, ch)

        if _same_app(_caret_app) and _inside_window(*caret_rect):
            return _anchor('caret', 'high', caret_rect, _caret_ts)

    # 3. Prefer focused-text
    focus_rect = (_focus_x, _focus_y, _focus_w, _focus_h)
    if _focus_w > 0 and _focus_h > 0 and now - _focus_ts < 20.0:
        fx, fy, fw, fh = focus_rect
        if (fx == 0 and fy == 0) or (fx < 2000 and fy < 2000 and not _inside_window(*focus_rect)):
            if _win_w > 0:
                focus_rect = (fx + _win_x, fy + _win_y, fw, fh)

        if _same_app(_focus_app) and _inside_window(*focus_rect):
            return _anchor('focused-text', 'medium', focus_rect, _focus_ts)

    # 4. Fallback to active window
    win = get_window_geometry()
    if win:
        return _anchor('window', 'low', win, _win_ts)

    return None
