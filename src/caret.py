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

# Active window bounds (updated on window:activate)
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

# Caret bounds (updated on object:text-caret-moved)
_caret_x: int = 0
_caret_y: int = 0
_caret_w: int = 0
_caret_h: int = 0
_caret_ts: float = 0.0
_caret_app: str = ''
_last_scan_ts: float = 0.0


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


def _on_window_event(event, _user_data) -> None:
    global _win_x, _win_y, _win_w, _win_h, _win_app, _win_ts
    try:
        obj = event.source
        if obj is None:
            return
        extents = obj.get_extents(_atspi.CoordType.SCREEN)
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


def get_window_geometry() -> Optional[tuple[int, int, int, int]]:
    """Return (x, y, width, height) of the last activated window, or None."""
    if _win_w > 0 and _win_h > 0:
        return (_win_x, _win_y, _win_w, _win_h)
    return None


def get_anchor() -> Optional[Anchor]:
    """Return the best current popup anchor with confidence metadata."""
    _refresh_from_desktop()
    now = time.monotonic()

    caret_rect = (_caret_x, _caret_y, _caret_w, _caret_h)
    if (
        _caret_w > 0 and _caret_h > 0
        and now - _caret_ts < 8.0
        and _same_app(_caret_app)
        and _inside_window(*caret_rect)
    ):
        return _anchor('caret', 'high', caret_rect, _caret_ts)

    focus_rect = (_focus_x, _focus_y, _focus_w, _focus_h)
    if (
        _focus_w > 0 and _focus_h > 0
        and now - _focus_ts < 20.0
        and _same_app(_focus_app)
        and _inside_window(*focus_rect)
    ):
        return _anchor('focused-text', 'medium', focus_rect, _focus_ts)

    win = get_window_geometry()
    if win:
        return _anchor('window', 'low', win, _win_ts)

    return None
