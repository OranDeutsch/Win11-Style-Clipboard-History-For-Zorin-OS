import subprocess
import hashlib
import json
import os
import shutil
import ctypes
from datetime import datetime
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkX11', '4.0')
from gi.repository import Gtk, Gdk, Adw, GLib, GdkX11, Pango


def _is_x11() -> bool:
    try:
        return isinstance(Gdk.Display.get_default(), GdkX11.X11Display)
    except Exception:
        return False


# True when running inside a Wayland session (even if GDK_BACKEND=x11 forces XWayland).
# On Wayland, xdotool XTEST events land on Mutter's focus proxy and go nowhere;
# ydotool (uinput) bypasses X11 entirely and reaches native Wayland windows.
_ON_WAYLAND: bool = bool(os.environ.get('WAYLAND_DISPLAY'))
_HAS_YDOTOOL: bool = shutil.which('ydotool') is not None
POPUP_WIDTH = 580
SETTINGS_PATH = Path.home() / '.local' / 'share' / 'clipboard-history' / 'settings.json'

# Positioning Modes
POSITION_OS_DEFAULT = 'os-default'
POSITION_CARET = 'caret'
POSITION_MOUSE = 'mouse'
POSITION_WINDOW = 'window'
POSITION_OPTIONS = (POSITION_OS_DEFAULT, POSITION_CARET, POSITION_MOUSE, POSITION_WINDOW)
POSITION_LABELS = {
    POSITION_OS_DEFAULT: 'OS default',
    POSITION_CARET: 'Caret',
    POSITION_MOUSE: 'Mouse',
    POSITION_WINDOW: 'Window',
}


class _XSizeHints(ctypes.Structure):
    _fields_ = [
        ('flags', ctypes.c_long),
        ('x', ctypes.c_int), ('y', ctypes.c_int),
        ('width', ctypes.c_int), ('height', ctypes.c_int),
        ('min_width', ctypes.c_int), ('min_height', ctypes.c_int),
        ('max_width', ctypes.c_int), ('max_height', ctypes.c_int),
        ('width_inc', ctypes.c_int), ('height_inc', ctypes.c_int),
        ('min_aspect_x', ctypes.c_int), ('min_aspect_y', ctypes.c_int),
        ('max_aspect_x', ctypes.c_int), ('max_aspect_y', ctypes.c_int),
        ('base_width', ctypes.c_int), ('base_height', ctypes.c_int),
        ('win_gravity', ctypes.c_int),
    ]


def _pre_map_window(xid: int, x: int, y: int) -> None:
    """Before mapping: move window, set USPosition hint, and set compositor opacity=0.

    XSync is called before XCloseDisplay to guarantee the X server has processed
    all three requests before GTK's XMapWindow arrives. XCloseDisplay alone only
    flushes (sends) but does not wait, leaving a race between two X connections.
    """
    try:
        lib = ctypes.CDLL('libX11.so.6')
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return

        # Move the window at the X level before it is mapped; works on unmapped windows.
        lib.XMoveWindow(dpy, xid, x, y)

        # WM_NORMAL_HINTS with USPosition — tells Mutter/XFWM where to place the window.
        hints = _XSizeHints()
        hints.flags = 1  # USPosition
        hints.x = x
        hints.y = y
        lib.XSetNormalHints(dpy, xid, ctypes.byref(hints))

        # _NET_WM_WINDOW_OPACITY = 0 — compositor renders fully transparent from frame 1.
        XA_CARDINAL = 6  # predefined X atom
        opacity_atom = lib.XInternAtom(dpy, b'_NET_WM_WINDOW_OPACITY', False)
        val = ctypes.c_uint32(0)
        lib.XChangeProperty(dpy, xid, opacity_atom, XA_CARDINAL, 32, 0,
                            ctypes.byref(val), 1)

        # XSync waits for the server to process all pending requests — the next map
        # request from GDK will therefore see the position and opacity already set.
        lib.XSync(dpy, False)
        lib.XCloseDisplay(dpy)
    except Exception as e:
        print(f'[popup] pre_map_window failed: {e}', flush=True)




def _monitor_for(x: int, y: int) -> Gdk.Rectangle:
    display = Gdk.Display.get_default()
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        m = monitors.get_item(i)
        g = m.get_geometry()
        if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
            return g
    return monitors.get_item(0).get_geometry()


def _restore_window_opacity(xid: int) -> None:
    """Delete _NET_WM_WINDOW_OPACITY so compositor reverts to fully opaque."""
    try:
        lib = ctypes.CDLL('libX11.so.6')
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return
        atom = lib.XInternAtom(dpy, b'_NET_WM_WINDOW_OPACITY', False)
        lib.XDeleteProperty(dpy, xid, atom)
        lib.XCloseDisplay(dpy)
    except Exception as e:
        print(f'[popup] restore_opacity failed: {e}', flush=True)


def _wl_copy_text(text: str) -> None:
    """Hand the text to wl-copy so it survives after our window hides."""
    try:
        proc = subprocess.Popen(
            ['wl-copy'],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        proc.stdin.write(text.encode('utf-8', errors='replace'))
        proc.stdin.close()
        # wl-copy daemonises; don't wait, but give it 200 ms to start up.
        import threading
        def _check():
            import time; time.sleep(0.2)
            rc = proc.poll()
            err = proc.stderr.read().decode(errors='replace').strip() if proc.stderr else ''
            if rc is not None and rc != 0:
                print(f'[popup] wl-copy exited rc={rc} err={err!r}', flush=True)
            else:
                print(f'[popup] wl-copy running pid={proc.pid}', flush=True)
        threading.Thread(target=_check, daemon=True).start()
    except FileNotFoundError:
        print('[popup] wl-copy not found', flush=True)
    except Exception as e:
        print(f'[popup] wl-copy text failed: {e}', flush=True)


def _wl_copy_image(path: str) -> None:
    """Hand image bytes to wl-copy."""
    try:
        with open(path, 'rb') as f:
            subprocess.Popen(
                ['wl-copy', '--type', 'image/png'],
                stdin=f,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'[popup] wl-copy image failed: {e}', flush=True)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _fallback_anchor() -> dict:
    display = Gdk.Display.get_default()
    monitors = display.get_monitors()
    geo = monitors.get_item(0).get_geometry()
    return {
        'source': 'fallback',
        'confidence': 'none',
        'x': geo.x,
        'y': geo.y,
        'width': geo.width,
        'height': geo.height,
        'age_ms': 0,
        'app': '',
    }


def _make_anchor(source: str, confidence: str, rect: tuple[int, int, int, int],
                 app: str = '', age_ms: int = 0) -> dict:
    x, y, w, h = rect
    return {
        'source': source,
        'confidence': confidence,
        'x': x,
        'y': y,
        'width': w,
        'height': h,
        'age_ms': age_ms,
        'app': app,
    }


def _mouse_anchor() -> dict | None:
    try:
        result = subprocess.run(
            ['xdotool', 'getmouselocation', '--shell'],
            capture_output=True,
            text=True,
            timeout=0.5
        )
        if result.returncode != 0:
            print(f'[popup] mouse anchor failed: {result.stderr.strip()!r}', flush=True)
            return None

        vals: dict[str, int] = {}
        for line in result.stdout.splitlines():
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key in ('X', 'Y'):
                vals[key] = int(value)

        if 'X' not in vals or 'Y' not in vals:
            return None

        return _make_anchor('mouse', 'manual', (vals['X'], vals['Y'], 1, 1))
    except Exception as e:
        print(f'[popup] mouse anchor failed: {e}', flush=True)
        return None


def _anchor_rect(anchor: dict) -> tuple[int, int, int, int]:
    return (
        int(anchor.get('x', 0)),
        int(anchor.get('y', 0)),
        max(1, int(anchor.get('width', 1))),
        max(1, int(anchor.get('height', 1))),
    )


def _popup_height_for_anchor(anchor: dict, ideal_h: int) -> int:
    x, y, w, h = _anchor_rect(anchor)
    source = anchor.get('source', 'fallback')
    geo = _monitor_for(x + w // 2, y + h // 2)
    margin = 10
    gap = 12

    if source in ('caret', 'focused-text'):
        space_below = max(0, (geo.y + geo.height - margin) - (y + h + gap))
        space_above = max(0, (y - gap) - (geo.y + margin))
        available = max(space_below, space_above)
    else:
        available = geo.height - margin * 2

    return min(ideal_h, max(200, available))


def _position_for_anchor(anchor: dict, popup_h: int) -> tuple[int, int]:
    """Place the popup according to anchor confidence, clamped to the monitor."""
    x, y, w, h = _anchor_rect(anchor)
    source = anchor.get('source', 'fallback')
    geo = _monitor_for(x + w // 2, y + h // 2)
    margin = 10
    gap = 12
    max_x = geo.x + geo.width - POPUP_WIDTH - margin
    max_y = geo.y + geo.height - popup_h - margin

    if source == 'fallback':
        px = geo.x + (geo.width - POPUP_WIDTH) // 2
        py = geo.y + (geo.height - popup_h) // 3
        return _clamp(px, geo.x + margin, max_x), _clamp(py, geo.y + margin, max_y)

    if source == 'window':
        # Low-confidence anchors should feel attached to the active app without
        # pretending we know the text insertion point.
        px = x + (w - POPUP_WIDTH) // 2
        py = y + max(36, min(180, h // 5))
        return _clamp(px, geo.x + margin, max_x), _clamp(py, geo.y + margin, max_y)

    # Caret/focused-text placement: try just below and to the right of the rect,
    # then flip horizontally/vertically only when the monitor edge requires it.
    px = x + w + gap
    if px + POPUP_WIDTH > geo.x + geo.width - margin:
        px = x - POPUP_WIDTH - gap

    py = y + h + gap
    if py + popup_h > geo.y + geo.height - margin:
        py = y - popup_h - gap

    return _clamp(px, geo.x + margin, max_x), _clamp(py, geo.y + margin, max_y)


def _get_active_window_class() -> str:
    # Try xdotool active window first.
    try:
        result = subprocess.run(
            ['xdotool', 'getactivewindow', 'getwindowclassname'],
            capture_output=True,
            text=True,
            timeout=0.5
        )
        cls = result.stdout.strip().lower()
        if cls:
            return cls
    except Exception as e:
        print(f'[popup] xdotool getactivewindow failed: {e}', flush=True)

    # Try xdotool focused window.
    try:
        result = subprocess.run(
            ['xdotool', 'getwindowfocus', 'getwindowclassname'],
            capture_output=True,
            text=True,
            timeout=0.5
        )
        cls = result.stdout.strip().lower()
        if cls:
            return cls
    except Exception as e:
        print(f'[popup] xdotool getwindowfocus failed: {e}', flush=True)

    # Fallback through xprop _NET_ACTIVE_WINDOW.
    try:
        root = subprocess.run(
            ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
            capture_output=True,
            text=True,
            timeout=0.5
        ).stdout

        # Example:
        # _NET_ACTIVE_WINDOW(WINDOW): window id # 0x3e00007
        if 'window id #' in root:
            win_id = root.split('window id #', 1)[1].strip().split()[0]

            wm = subprocess.run(
                ['xprop', '-id', win_id, 'WM_CLASS'],
                capture_output=True,
                text=True,
                timeout=0.5
            ).stdout

            # Example:
            # WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"
            if '=' in wm:
                parts = wm.split('=', 1)[1].replace('"', '').split(',')
                parts = [p.strip().lower() for p in parts if p.strip()]
                if parts:
                    return parts[-1]
    except Exception as e:
        print(f'[popup] xprop fallback failed: {e}', flush=True)

    return ''


def _is_terminal(wm_class: str) -> bool:
    wm_class = (wm_class or '').lower()

    terminal_classes = {
        'gnome-terminal',
        'gnome-terminal-server',
        'org.gnome.terminal',
        'gnome-terminal-server.gnome-terminal',
        'kgx',
        'org.gnome.console',
        'tilix',
        'alacritty',
        'kitty',
        'xterm',
        'uxterm',
        'rxvt',
        'konsole',
        'terminator',
        'termite',
        'st',
        'foot',
        'wezterm',
        'hyper',
        'ghostty',
        'com.mitchellh.ghostty',
    }

    return wm_class in terminal_classes or 'terminal' in wm_class


def _is_vscode_terminal_context(wm_class: str, focus_hints: dict | None) -> bool:
    if (wm_class or '').lower() not in {'code', 'code-oss'}:
        return False
    if not focus_hints:
        return False
    haystack = ' '.join(
        str(focus_hints.get(key, '') or '').lower()
        for key in ('app', 'role', 'name', 'description')
    )
    return 'terminal' in haystack


def _paste_key_for(wm_class: str) -> str:
    return 'ctrl+shift+v' if _is_terminal(wm_class) else 'ctrl+v'


class ClipboardPopup(Adw.Window):
    def __init__(self, app, db, monitor, caret_tracker=None):
        super().__init__()
        self.set_application(app)
        self.set_title('Clipboard History')
        self.set_default_size(POPUP_WIDTH, 750)
        self.set_resizable(False)
        self._db = db
        self._monitor = monitor
        self._caret_tracker = caret_tracker
        self._active_wm_class = ''
        self._focus_hints: dict[str, str] = {}
        self._x11_configured = False  # skip-taskbar hint only needed once
        self._position_mode = POSITION_OS_DEFAULT
        self._wayland_positioning = False
        self._load_settings()
        self._position_buttons: dict[str, Gtk.CheckButton] = {}

        self._build_ui()
        self._connect_signals()

        # Eagerly realize so we have an X11 XID before the first present().
        self.realize()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Load custom CSS
        css_path = Path(__file__).parent / 'style.css'
        if css_path.exists():
            provider = Gtk.CssProvider()
            provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        toolbar_view = Adw.ToolbarView()

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_back_button(False)

        settings_btn = Gtk.MenuButton()
        settings_btn.set_icon_name('emblem-system-symbolic')
        settings_btn.set_tooltip_text('Settings')
        settings_btn.add_css_class('flat')
        settings_btn.add_css_class('circular')
        settings_btn.set_popover(self._make_settings_popover())
        header.pack_end(settings_btn)

        clear_btn = Gtk.Button()
        clear_btn.set_icon_name('edit-clear-all-symbolic')
        clear_btn.set_tooltip_text('Clear unpinned history')
        clear_btn.add_css_class('flat')
        clear_btn.connect('clicked', self._on_clear)
        header.pack_end(clear_btn)

        # Search entry
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.connect('search-changed', self._on_search_changed)
        self._search_entry.connect('activate', self._on_search_activate)
        header.set_title_widget(self._search_entry)

        toolbar_view.add_top_bar(header)

        # Scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_min_content_height(200)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self._list_box.add_css_class('boxed-list')
        self._list_box.set_margin_top(6)
        self._list_box.set_margin_bottom(6)
        self._list_box.set_margin_start(8)
        self._list_box.set_margin_end(8)
        self._list_box.set_filter_func(self._filter_row)
        self._list_box.connect('row-activated', self._on_row_activated)

        scroll.set_child(self._list_box)

        # Empty state
        self._empty_label = Gtk.Label(
            label='No clipboard history yet.\nCopy something to get started.'
        )
        self._empty_label.set_justify(Gtk.Justification.CENTER)
        self._empty_label.add_css_class('dim-label')
        self._empty_label.set_margin_top(40)
        self._empty_label.set_margin_bottom(40)
        self._empty_label.set_visible(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(scroll)
        outer.append(self._empty_label)
        toolbar_view.set_content(outer)

        self.set_content(toolbar_view)

    def _make_settings_popover(self) -> Gtk.Popover:
        """Create the settings popover with positioning options."""
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(14); box.set_margin_end(14)

        title = Gtk.Label(label='Settings'); title.set_halign(Gtk.Align.START); title.add_css_class('heading')
        box.append(title)

        self._position_buttons = {}
        group = None
        for mode in POSITION_OPTIONS:
            btn = Gtk.CheckButton(label=POSITION_LABELS[mode], halign=Gtk.Align.START)
            if group: btn.set_group(group)
            else: group = btn
            btn.set_active(mode == self._position_mode)
            btn.connect('toggled', self._on_position_mode_toggled, mode)
            self._position_buttons[mode] = btn
            box.append(btn)

        if _ON_WAYLAND:
            box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
            wl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            wl_lbl = Gtk.Label(label='Wayland Caret Positioning\n(Experimental)', halign=Gtk.Align.START)
            wl_lbl.add_css_class('caption')
            wl_switch = Gtk.Switch(active=self._wayland_positioning, valign=Gtk.Align.CENTER)
            wl_switch.connect('notify::active', self._on_wayland_pos_toggled)
            wl_box.append(wl_lbl); wl_box.append(wl_switch)
            box.append(wl_box)

        # Delete All History Button
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        del_all_btn = Gtk.Button(label='Delete All History')
        del_all_btn.add_css_class('destructive-action')
        del_all_btn.connect('clicked', self._on_delete_all_clicked)
        box.append(del_all_btn)

        popover.set_child(box)
        return popover

    def _on_delete_all_clicked(self, btn):
        dialog = Adw.AlertDialog.new('Delete All History?', 'This will permanently remove ALL clipboard entries, including pinned ones.')
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('delete', 'Delete All')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_delete_all_confirmed)
        dialog.present(self)

    def _on_delete_all_confirmed(self, dialog, response):
        if response == 'delete':
            self._db.clear_unpinned() # Clear unpinned first
            # We need a clear_all method in DB or just delete where 1=1
            # I'll update db.py if needed, but for now I'll use toggle_pin on everything and then clear_unpinned?
            # Better to add clear_all to db.py.
            self._db.clear_all()
            self._populate()

    def _load_settings(self) -> None:
        try:
            if not SETTINGS_PATH.exists():
                return
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            mode = settings.get('popup_position', POSITION_OS_DEFAULT)
            if mode in POSITION_OPTIONS:
                self._position_mode = mode
            self._wayland_positioning = bool(settings.get('wayland_positioning', False))
        except Exception as e:
            print(f'[popup] settings load failed: {e}', flush=True)

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
            if SETTINGS_PATH.exists():
                try:
                    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                        settings = json.load(f)
                except Exception:
                    settings = {}
            settings['popup_position'] = self._position_mode
            settings['wayland_positioning'] = self._wayland_positioning
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
                f.write('\n')
        except Exception as e:
            print(f'[popup] settings save failed: {e}', flush=True)

    def _connect_signals(self):
        # Close on focus loss
        self.connect('notify::is-active', self._on_active_changed)

        # Keyboard navigation
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

    # ── Showing / Positioning ────────────────────────────────────────────────

    def show_at_best_position(self):
        """Show popup near the best available contextual anchor."""
        import os as _os
        disp = Gdk.Display.get_default()
        surface = self.get_surface()
        print(f'[popup] GDK backend: {type(disp).__name__}', flush=True)
        print(f'[popup] surface type: {type(surface).__name__}', flush=True)
        print(f'[popup] DISPLAY={_os.environ.get("DISPLAY")!r}  '
              f'WAYLAND_DISPLAY={_os.environ.get("WAYLAND_DISPLAY")!r}  '
              f'GDK_BACKEND={_os.environ.get("GDK_BACKEND")!r}', flush=True)

        self._active_wm_class = _get_active_window_class()
        if not self._active_wm_class and self._caret_tracker:
            self._active_wm_class = self._caret_tracker.get_app_name()
        self._focus_hints = {}
        if self._caret_tracker:
            try:
                self._focus_hints = self._caret_tracker.get_focus_hints()
            except Exception as e:
                print(f'[popup] focus hints failed: {e}', flush=True)
        print(f'[popup] active wm_class={self._active_wm_class!r}', flush=True)
        if self._focus_hints:
            print(f'[popup] focus hints={self._focus_hints!r}', flush=True)

        n = len(self._db.get_entries(limit=100))
        ideal_h = min(750, max(200, n * 80 + 80))
        self.set_default_size(POPUP_WIDTH, ideal_h)

        if self._position_mode == POSITION_OS_DEFAULT and not self._wayland_positioning:
            print('[popup] position mode: OS default; letting window manager place popup', flush=True)
            self._populate()
            self.present()
            GLib.idle_add(self._reveal)
            return

        anchor = self._best_anchor()
        popup_h = _popup_height_for_anchor(anchor, ideal_h)
        self.set_default_size(POPUP_WIDTH, popup_h)
        px, py = _position_for_anchor(anchor, popup_h)
        self._target_x, self._target_y = px, py
        print(
            '[popup] anchor: '
            f"source={anchor.get('source')!r} "
            f"confidence={anchor.get('confidence')!r} "
            f"rect=({anchor.get('x')},{anchor.get('y')} "
            f"{anchor.get('width')}x{anchor.get('height')}) "
            f"age_ms={anchor.get('age_ms')}",
            flush=True,
        )
        print(f'[popup] target position: ({px}, {py})  popup_h={popup_h}', flush=True)

        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            print(f'[popup] X11 surface xid=0x{xid:x} — calling pre_map_window', flush=True)
            _pre_map_window(xid, px, py)
        elif self._wayland_positioning:
            print('[popup] Wayland surface with experimental positioning', flush=True)
            self._wl_move(px, py)
        else:
            print('[popup] Wayland surface — skipping X11 positioning', flush=True)

        self._populate()
        self.present()
        GLib.idle_add(self._move_and_reveal)

    def _best_anchor(self) -> dict:
        """Return caret/focused-text/window/fallback anchor metadata."""
        if self._position_mode == POSITION_CARET:
            if self._caret_tracker:
                anchor = self._caret_tracker.get_anchor()
                if anchor and anchor.get('source') in ('caret', 'ibus', 'focused-text'):
                    return anchor
            print('[popup] caret placement unavailable; falling back', flush=True)

        if self._position_mode == POSITION_MOUSE:
            anchor = _mouse_anchor()
            if anchor:
                return anchor
            print('[popup] mouse placement unavailable; falling back', flush=True)

        if self._position_mode == POSITION_WINDOW:
            if self._caret_tracker:
                try:
                    win = self._caret_tracker.get_window_geometry()
                    if win:
                        return _make_anchor(
                            'window',
                            'manual',
                            win,
                            self._caret_tracker.get_app_name()
                        )
                except Exception as e:
                    print(f'[popup] window anchor failed: {e}', flush=True)
            print('[popup] window placement unavailable; falling back', flush=True)

        if self._caret_tracker:
            try:
                anchor = self._caret_tracker.get_anchor()
                if anchor:
                    return anchor
            except Exception as e:
                print(f'[popup] caret anchor failed: {e}', flush=True)
        print('[popup] fallback: primary monitor command palette', flush=True)
        return _fallback_anchor()

    def _on_position_mode_toggled(self, btn, mode: str):
        if not btn.get_active() or mode == self._position_mode:
            return
        self._position_mode = mode
        self._save_settings()
        print(f'[popup] popup_position={mode}', flush=True)

    def _on_wayland_pos_toggled(self, sw, pspec):
        self._wayland_positioning = sw.get_active()
        self._save_settings()
        print(f'[popup] wayland_positioning={self._wayland_positioning}', flush=True)

    def _move_and_reveal(self) -> bool:
        self._xmove(self._target_x, self._target_y)
        GLib.timeout_add(60, self._retry_xmove)
        GLib.timeout_add(160, self._retry_xmove)
        GLib.timeout_add(240, self._reveal)  # give compositor time to apply the move
        return False

    def _retry_xmove(self) -> bool:
        self._xmove(self._target_x, self._target_y)
        return False

    def _reveal(self) -> bool:
        surface = self.get_surface()
        if isinstance(surface, GdkX11.X11Surface):
            _restore_window_opacity(surface.get_xid())
        first = self._list_box.get_row_at_index(0)
        if first:
            self._list_box.select_row(first)
        self._search_entry.grab_focus()
        return False

    def _xmove(self, px: int, py: int):
        """Move the X11 window to (px, py). No-op on Wayland."""
        surface = self.get_surface()
        if not isinstance(surface, GdkX11.X11Surface):
            # Try Wayland move if enabled
            if self._wayland_positioning:
                self._wl_move(px, py)
            return

        xid = surface.get_xid()
        r = subprocess.run(
            ['xdotool', 'windowmove', str(xid), str(px), str(py)],
            capture_output=True, text=True, timeout=0.5
        )
        print(f'[popup] xdotool windowmove 0x{xid:x} ({px},{py}): '
              f'rc={r.returncode} {r.stderr.strip()!r}', flush=True)

        # Apply X11 hints once.
        if not self._x11_configured:
            surface.set_skip_taskbar_hint(True)
            surface.set_skip_pager_hint(True)
            self._x11_configured = True

    def _wl_move(self, px: int, py: int):
        """Attempt to move the window on Wayland via GNOME Shell extension DBus."""
        if not _ON_WAYLAND:
            return
        try:
            # This requires a companion GNOME Shell extension to be installed
            # that provides org.gnome.Shell.MoveWindow.
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # Placeholder for future extension call
            # bus.call_sync(...)
            pass
        except Exception:
            pass

    # ── List population ──────────────────────────────────────────────────────

    def _populate(self):
        self._search_entry.set_text('')
        child = self._list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        entries = self._db.get_entries()
        for entry in entries:
            row = self._make_row(entry)
            self._list_box.append(row)

        self._empty_label.set_visible(len(entries) == 0)

    def _make_row(self, entry) -> Gtk.ListBoxRow:
        id_, type_, content, image_path, pinned, created_at = entry

        row = Gtk.ListBoxRow()
        row._entry_id = id_
        row._content_type = type_
        row._content = content
        row._image_path = image_path
        row._pinned = bool(pinned)
        row._search_text = (content or '').lower()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_start(4)
        box.set_margin_end(2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Content area
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        content_box.set_hexpand(True)
        content_box.set_valign(Gtk.Align.CENTER)

        if type_ == 'image' and image_path and os.path.exists(image_path):
            pic = Gtk.Picture.new_for_filename(image_path)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_size_request(220, 80)
            pic.set_halign(Gtk.Align.START)
            content_box.append(pic)
        else:
            preview = (content or '[empty]')[:150].replace('\n', ' ').replace('\t', ' ')
            lbl = Gtk.Label(label=preview)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_xalign(0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(45)
            lbl.add_css_class('ch-preview')
            if pinned:
                lbl.add_css_class('ch-pinned-text')
            content_box.append(lbl)

        # Timestamp
        ts = datetime.fromtimestamp(created_at).strftime('%H:%M')
        ts_lbl = Gtk.Label(label=ts)
        ts_lbl.set_halign(Gtk.Align.START)
        ts_lbl.add_css_class('caption')
        ts_lbl.add_css_class('dim-label')
        content_box.append(ts_lbl)

        box.append(content_box)

        # Pin button
        pin_btn = Gtk.Button()
        pin_btn.set_icon_name('view-pin-symbolic')
        pin_btn.add_css_class('flat')
        pin_btn.add_css_class('circular')
        if pinned:
            pin_btn.add_css_class('accent')
        pin_btn.set_tooltip_text('Unpin' if pinned else 'Pin')
        pin_btn.set_valign(Gtk.Align.CENTER)
        pin_btn.connect('clicked', self._on_pin_clicked, row)
        box.append(pin_btn)

        # Delete button
        del_btn = Gtk.Button()
        del_btn.set_icon_name('edit-delete-symbolic')
        del_btn.add_css_class('flat')
        del_btn.add_css_class('circular')
        del_btn.set_tooltip_text('Delete')
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect('clicked', self._on_delete_clicked, row)
        box.append(del_btn)

        row.set_child(box)
        return row

    # ── Filtering ────────────────────────────────────────────────────────────

    def _filter_row(self, row) -> bool:
        query = self._search_entry.get_text().lower().strip()
        if not query:
            return True
        return query in getattr(row, '_search_text', '')

    def _on_search_changed(self, entry):
        self._list_box.invalidate_filter()

        # Select first visible row
        row = self._list_box.get_row_at_index(0)
        while row and not row.get_visible():
            row = row.get_next_sibling()
        if row:
            self._list_box.select_row(row)

    def _on_search_activate(self, entry):
        row = self._list_box.get_selected_row()
        if row:
            self._paste_row(row)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_row_activated(self, list_box, row):
        self._paste_row(row)

    def _paste_row(self, row):
        type_ = row._content_type
        content = row._content
        image_path = row._image_path
        wm_class = self._active_wm_class
        is_terminal = (
            type_ == 'text'
            and content
            and (
                _is_terminal(wm_class)
                or _is_vscode_terminal_context(wm_class, self._focus_hints)
            )
        )
        paste_delay_ms = 700 if is_terminal else 400

        clipboard = Gdk.Display.get_default().get_clipboard()

        # Set clipboard BEFORE hiding the window so GDK still owns the X11
        # selection when it is claimed. wl-copy runs as a daemon and keeps
        # the content alive on the Wayland side after our window disappears.
        if type_ == 'text' and content:
            paste_text = content.rstrip('\n') if is_terminal else content
            h = hashlib.sha256(paste_text.encode()).hexdigest()
            self._monitor.set_skip_hash(h)
            clipboard.set(paste_text)
            _wl_copy_text(paste_text)
        elif type_ == 'image' and image_path and os.path.exists(image_path):
            try:
                texture = Gdk.Texture.new_from_filename(image_path)
                self._monitor.set_skip_hash('image')
                clipboard.set(texture)
                _wl_copy_image(image_path)
            except Exception as e:
                print(f'[popup] set image failed: {e}', flush=True)
                return

        self.set_visible(False)

        # Terminals use Ctrl+Shift+V and get a slightly longer delay because focus
        # can briefly bounce while the popup hides under GNOME/Mutter.
        if is_terminal:
            key = 'ctrl+shift+v'
            print(f'[popup] terminal paste into {wm_class!r} using {key}', flush=True)
        else:
            key = _paste_key_for(wm_class)
            print(f'[popup] normal paste into {wm_class!r} using {key}', flush=True)
        GLib.timeout_add(paste_delay_ms, self._do_paste, key)

    def _do_paste(self, key: str) -> bool:
        if _ON_WAYLAND:
            # Try persistent virtual keyboard (evdev/uinput) first — it works for
            # both Wayland-native and XWayland windows and has no device-timing race.
            import uinput_kbd
            if uinput_kbd.inject_key(key):
                return False
            # Fall back to ydotool (standalone uinput — may have timing issues)
            if _HAS_YDOTOOL:
                self._do_paste_ydotool(key)
                return False
        # X11 session (or Wayland fallback exhausted): XTEST via xdotool
        self._do_paste_xdotool(key)
        return False

    def _do_paste_ydotool(self, key: str) -> None:
        """Inject key via ydotool (uinput) — fallback when evdev is unavailable."""
        r = subprocess.run(
            ['ydotool', 'key', '--delay', '0', key],
            capture_output=True, text=True, timeout=3.0
        )
        if r.returncode != 0:
            print(f'[popup] ydotool key {key!r} FAILED rc={r.returncode} '
                  f'stderr={r.stderr.strip()!r} — falling back to xdotool', flush=True)
            self._do_paste_xdotool(key)
        else:
            print(f'[popup] ydotool key {key!r}: OK', flush=True)

    def _do_paste_xdotool(self, key: str) -> None:
        """Inject key via xdotool XTEST — works for X11 / XWayland windows."""
        r = subprocess.run(
            ['xdotool', 'key', '--clearmodifiers', key],
            capture_output=True, text=True, timeout=2.0
        )
        if r.returncode != 0:
            print(f'[popup] xdotool key {key!r} FAILED rc={r.returncode} '
                  f'stderr={r.stderr.strip()!r}', flush=True)
        else:
            print(f'[popup] xdotool key {key!r}: OK', flush=True)

    def _on_pin_clicked(self, btn, row):
        self._db.toggle_pin(row._entry_id)
        row._pinned = not row._pinned
        if row._pinned:
            btn.add_css_class('accent')
            btn.set_tooltip_text('Unpin')
        else:
            btn.remove_css_class('accent')
            btn.set_tooltip_text('Pin')

        # Re-sort list
        self._populate()

    def _on_delete_clicked(self, btn, row):
        self._db.delete_entry(row._entry_id)
        self._list_box.remove(row)
        entries = self._db.get_entries()
        self._empty_label.set_visible(len(entries) == 0)

    def _on_clear(self, btn):
        dialog = Adw.AlertDialog.new(
            'Clear History?',
            'All unpinned clipboard entries will be deleted. Pinned items are kept.'
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('clear', 'Clear')
        dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.connect('response', self._on_clear_confirmed)
        dialog.present(self)

    def _on_clear_confirmed(self, dialog, response):
        if response == 'clear':
            self._db.clear_unpinned()
            self._populate()

    # ── Focus / Keyboard ─────────────────────────────────────────────────────

    def _on_active_changed(self, window, pspec):
        if not self.is_active():
            GLib.timeout_add(100, self._check_still_inactive)

    def _check_still_inactive(self) -> bool:
        # Confirm still inactive before hiding.
        if not self.is_active():
            self.set_visible(False)
        return False

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        # Let search entry handle normal typing
        if self._search_entry.has_focus() and keyval not in (
            Gdk.KEY_Escape,
            Gdk.KEY_Return,
            Gdk.KEY_KP_Enter,
            Gdk.KEY_Up,
            Gdk.KEY_Down,
            Gdk.KEY_Delete
        ):
            return False

        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self._list_box.get_selected_row()
            if row:
                self._paste_row(row)
            return True

        if keyval == Gdk.KEY_Delete:
            row = self._list_box.get_selected_row()
            if row:
                self._on_delete_clicked(None, row)
            return True

        if keyval in (Gdk.KEY_p, Gdk.KEY_P):
            row = self._list_box.get_selected_row()
            if row:
                self._on_pin_clicked(None, row)
            return True

        if keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True

        if keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True

        return False

    def _move_selection(self, direction: int):
        current = self._list_box.get_selected_row()
        if current is None:
            first = self._list_box.get_row_at_index(0)
            if first:
                self._list_box.select_row(first)
            return

        idx = current.get_index()
        target_idx = idx + direction
        target = self._list_box.get_row_at_index(target_idx)
        if target:
            self._list_box.select_row(target)
            target.grab_focus()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _iter_rows(self):
        row = self._list_box.get_first_child()
        while row:
            yield row
            row = row.get_next_sibling()
