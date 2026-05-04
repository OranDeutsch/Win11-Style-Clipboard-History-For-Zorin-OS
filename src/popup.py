"""
Main UI and Popup Logic for Clipboard History.

This module handles the GTK4/libadwaita interface, window positioning strategies,
and the final paste injection via virtual keyboard.
"""
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
    """Check if the current GDK display is X11."""
    try:
        return isinstance(Gdk.Display.get_default(), GdkX11.X11Display)
    except Exception:
        return False


# Detect Wayland environment (even if running via XWayland)
_ON_WAYLAND: bool = bool(os.environ.get('WAYLAND_DISPLAY'))
_HAS_YDOTOOL: bool = shutil.which('ydotool') is not None

POPUP_WIDTH = 580
SETTINGS_PATH = Path.home() / '.local' / 'share' / 'clipboard-history' / 'settings.json'
BIN_DIR = Path.home() / '.local' / 'bin'
SHORTCUT_COMMAND = str(BIN_DIR / 'clipboard-history-show')
MK_SCHEMA = 'org.gnome.settings-daemon.plugins.media-keys'
CH_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/'
CH_SCHEMA = f'{MK_SCHEMA}.custom-keybinding:{CH_PATH}'
DEFAULT_HOTKEY = '<Super>v'

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


# --- X11 Positioning Hacks ---

class _XSizeHints(ctypes.Structure):
    """Ctype structure for X11 WM_NORMAL_HINTS."""
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
    """Prepare X11 window before mapping to ensure correct placement and opacity.

    This bypasses GTK's default placement to force the window at (x, y) 
    using USPosition hints and sets initial opacity to 0 to prevent flicker 
    during move adjustments.
    """
    try:
        lib = ctypes.CDLL('libX11.so.6')
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return

        # Force move at the X level
        lib.XMoveWindow(dpy, xid, x, y)

        # Set WM_NORMAL_HINTS with USPosition — tells Mutter/XFWM where to place the window.
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

        # XSync waits for the server to process all pending requests.
        lib.XSync(dpy, False)
        lib.XCloseDisplay(dpy)
    except Exception as e:
        print(f'[popup] pre_map_window failed: {e}', flush=True)


def _monitor_for(x: int, y: int) -> Gdk.Rectangle:
    """Find the monitor geometry that contains the given coordinates."""
    display = Gdk.Display.get_default()
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        m = monitors.get_item(i)
        g = m.get_geometry()
        if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
            return g
    return monitors.get_item(0).get_geometry()


def _restore_window_opacity(xid: int) -> None:
    """Restore X11 window opacity to fully opaque."""
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
    """Copy text to clipboard using wl-copy (Wayland native persistence)."""
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
    """Copy image to clipboard using wl-copy."""
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
    """Clamp a value between low and high."""
    return max(low, min(value, high))


def _fallback_anchor() -> dict:
    """Default anchor at the top-center of the primary monitor."""
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
    """Helper to construct an anchor dictionary."""
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
    """Get anchor at current mouse position via xdotool."""
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
            if key in ('X', 'Y', 'WINDOW'):
                vals[key] = int(value)

        if 'X' not in vals or 'Y' not in vals:
            return None

        anchor = _make_anchor('mouse', 'manual', (vals['X'], vals['Y'], 1, 1))
        if vals.get('WINDOW', 0) > 0:
            anchor['window_id'] = vals['WINDOW']
        return anchor
    except Exception as e:
        print(f'[popup] mouse anchor failed: {e}', flush=True)
        return None


def _anchor_rect(anchor: dict) -> tuple[int, int, int, int]:
    """Extract rectangle tuple from anchor dict."""
    return (
        int(anchor.get('x', 0)),
        int(anchor.get('y', 0)),
        max(1, int(anchor.get('width', 1))),
        max(1, int(anchor.get('height', 1))),
    )


def _popup_height_for_anchor(anchor: dict, ideal_h: int) -> int:
    """Calculate available height for the popup based on its monitor position."""
    x, y, w, h = _anchor_rect(anchor)
    source = anchor.get('source', 'fallback')
    geo = _monitor_for(x + w // 2, y + h // 2)
    margin = 10
    gap = 12

    if source in ('caret', 'focused-text', 'ibus'):
        space_below = max(0, (geo.y + geo.height - margin) - (y + h + gap))
        space_above = max(0, (y - gap) - (geo.y + margin))
        available = max(space_below, space_above)
    else:
        available = geo.height - margin * 2

    return min(ideal_h, max(200, available))


def _position_for_anchor(anchor: dict, popup_h: int) -> tuple[int, int]:
    """Determine (px, py) coordinates for the popup relative to an anchor."""
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
        # Low-confidence anchors should feel attached to the active app
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
    """Get the WM_CLASS of the currently focused window."""
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
    except Exception:
        pass

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
    except Exception:
        pass

    return ''


def _get_window_class(window_id: int) -> str:
    try:
        result = subprocess.run(
            ['xdotool', 'getwindowclassname', str(window_id)],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        return result.stdout.strip().lower()
    except Exception:
        return ''


def _run_gsettings(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['gsettings', *args],
        capture_output=True,
        text=True,
        timeout=1.0,
    )


def _get_hotkey() -> str:
    try:
        result = _run_gsettings(['get', CH_SCHEMA, 'binding'])
        if result.returncode == 0:
            return result.stdout.strip().strip("'") or DEFAULT_HOTKEY
    except Exception as e:
        print(f'[popup] hotkey read failed: {e}', flush=True)
    return DEFAULT_HOTKEY


def _set_hotkey(binding: str) -> bool:
    try:
        result = _run_gsettings(['get', MK_SCHEMA, 'custom-keybindings'])
        paths = []
        if result.returncode == 0:
            paths = [
                p.strip().strip("'")
                for p in result.stdout.strip().strip('[]').split(',')
                if p.strip()
            ]

        for path in paths:
            if path == CH_PATH:
                continue
            schema = f'{MK_SCHEMA}.custom-keybinding:{path}'
            current = _run_gsettings(['get', schema, 'binding'])
            if current.returncode == 0 and current.stdout.strip() == f"'{binding}'":
                _run_gsettings(['set', schema, 'binding', "''"])

        _run_gsettings(['set', CH_SCHEMA, 'name', 'Clipboard History'])
        _run_gsettings(['set', CH_SCHEMA, 'command', SHORTCUT_COMMAND])
        bind = _run_gsettings(['set', CH_SCHEMA, 'binding', f"'{binding}'"])
        if bind.returncode != 0:
            print(f'[popup] hotkey set failed: {bind.stderr.strip()!r}', flush=True)
            return False

        if CH_PATH not in paths:
            paths.append(CH_PATH)
            formatted = '[' + ', '.join(f"'{p}'" for p in paths) + ']'
            _run_gsettings(['set', MK_SCHEMA, 'custom-keybindings', formatted])

        print(f'[popup] hotkey={binding}', flush=True)
        return True
    except Exception as e:
        print(f'[popup] hotkey set failed: {e}', flush=True)
        return False


def _event_to_accelerator(keyval: int, state: Gdk.ModifierType) -> str | None:
    if keyval in (
        Gdk.KEY_Escape,
        Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
        Gdk.KEY_Control_L, Gdk.KEY_Control_R,
        Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
        Gdk.KEY_Super_L, Gdk.KEY_Super_R,
        Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
    ):
        return None

    mods = state & Gtk.accelerator_get_default_mod_mask()
    if not (mods & (Gdk.ModifierType.CONTROL_MASK |
                    Gdk.ModifierType.ALT_MASK |
                    Gdk.ModifierType.SHIFT_MASK |
                    Gdk.ModifierType.SUPER_MASK |
                    Gdk.ModifierType.META_MASK)):
        return None
    return Gtk.accelerator_name(keyval, mods)


def _is_terminal(wm_class: str) -> bool:
    """Check if the given window class represents a terminal emulator."""
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
    """Heuristic to detect if focus is inside VS Code's integrated terminal."""
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
    """Determine the paste shortcut (Ctrl+V vs Ctrl+Shift+V)."""
    return 'ctrl+shift+v' if _is_terminal(wm_class) else 'ctrl+v'


class ClipboardPopup(Adw.Window):
    """The main clipboard history picker window."""

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
        self._paste_window_id: int | None = None
        self._x11_configured = False
        self._position_mode = POSITION_OS_DEFAULT
        self._settings_popover: Gtk.Popover | None = None
        self._dialog_open = False
        self._capturing_hotkey = False
        self._hotkey_label: Gtk.Label | None = None
        self._hotkey_button: Gtk.Button | None = None
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
        self._settings_popover = self._make_settings_popover()
        settings_btn.set_popover(self._settings_popover)
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

        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))

        hotkey_title = Gtk.Label(label='Keyboard Shortcut')
        hotkey_title.set_halign(Gtk.Align.START)
        hotkey_title.add_css_class('heading')
        box.append(hotkey_title)

        hotkey_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hotkey_box.set_halign(Gtk.Align.FILL)

        self._hotkey_label = Gtk.Label(label=_get_hotkey(), halign=Gtk.Align.START, xalign=0)
        self._hotkey_label.set_hexpand(True)
        self._hotkey_label.add_css_class('caption')
        hotkey_box.append(self._hotkey_label)

        self._hotkey_button = Gtk.Button(label='Set Shortcut')
        self._hotkey_button.connect('clicked', self._on_capture_hotkey_clicked)
        hotkey_box.append(self._hotkey_button)

        reset_btn = Gtk.Button(label='Reset')
        reset_btn.connect('clicked', self._on_reset_hotkey_clicked)
        hotkey_box.append(reset_btn)

        box.append(hotkey_box)

        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        del_all_btn = Gtk.Button(label='Delete All History')
        del_all_btn.add_css_class('destructive-action')
        del_all_btn.connect('clicked', self._on_delete_all_clicked)
        box.append(del_all_btn)

        popover.set_child(box)
        return popover

    def _on_delete_all_clicked(self, btn):
        self._close_settings_popover()
        self._dialog_open = True
        dialog = Adw.AlertDialog.new('Delete All History?', 'This will permanently remove ALL clipboard entries, including pinned ones.')
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('delete', 'Delete All')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_delete_all_confirmed)
        dialog.present(self)

    def _on_delete_all_confirmed(self, dialog, response):
        self._dialog_open = False
        if response == 'delete':
            self._db.clear_all()
            self._populate()

    def _on_capture_hotkey_clicked(self, btn):
        self._capturing_hotkey = True
        if self._hotkey_label:
            self._hotkey_label.set_text('Press a shortcut...')
        if self._hotkey_button:
            self._hotkey_button.set_label('Listening')
            self._hotkey_button.set_sensitive(False)

    def _finish_hotkey_capture(self, binding: str | None) -> None:
        self._capturing_hotkey = False
        if binding and _set_hotkey(binding):
            current = binding
        else:
            current = _get_hotkey()
        if self._hotkey_label:
            self._hotkey_label.set_text(current)
        if self._hotkey_button:
            self._hotkey_button.set_label('Set Shortcut')
            self._hotkey_button.set_sensitive(True)

    def _on_reset_hotkey_clicked(self, btn):
        self._finish_hotkey_capture(DEFAULT_HOTKEY)

    def _load_settings(self) -> None:
        try:
            if not SETTINGS_PATH.exists():
                return
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            mode = settings.get('popup_position', POSITION_OS_DEFAULT)
            if mode in POSITION_OPTIONS:
                self._position_mode = mode
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

        self._active_wm_class = _get_active_window_class()
        if self._caret_tracker:
            tracked_app = self._caret_tracker.get_app_name()
            if (not self._active_wm_class or self._active_wm_class == 'gnome-shell') and tracked_app != 'gnome-shell':
                self._active_wm_class = tracked_app
        self._focus_hints = {}
        if self._caret_tracker:
            try:
                self._focus_hints = self._caret_tracker.get_focus_hints()
            except Exception as e:
                print(f'[popup] focus hints failed: {e}', flush=True)

        n = len(self._db.get_entries(limit=100))
        ideal_h = min(750, max(200, n * 80 + 80))
        self.set_default_size(POPUP_WIDTH, ideal_h)

        if self._position_mode == POSITION_OS_DEFAULT:
            print('[popup] position mode: OS default; letting window manager place popup', flush=True)
            self._populate()
            self.present()
            GLib.idle_add(self._reveal)
            return

        anchor = self._best_anchor()
        self._paste_window_id = anchor.get('window_id') if anchor.get('source') == 'mouse' else None
        if self._paste_window_id:
            mouse_wm_class = _get_window_class(self._paste_window_id)
            if mouse_wm_class:
                self._active_wm_class = mouse_wm_class
        popup_h = _popup_height_for_anchor(anchor, ideal_h)
        self.set_default_size(POPUP_WIDTH, popup_h)
        px, py = _position_for_anchor(anchor, popup_h)
        self._target_x, self._target_y = px, py

        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            _pre_map_window(xid, px, py)

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
        return _fallback_anchor()

    def _on_position_mode_toggled(self, btn, mode: str):
        if not btn.get_active() or mode == self._position_mode:
            return
        self._position_mode = mode
        self._save_settings()
        print(f'[popup] popup_position={mode}', flush=True)

    def _close_settings_popover(self) -> None:
        if self._settings_popover and self._settings_popover.get_visible():
            self._settings_popover.popdown()

    def _hide_popup(self) -> None:
        self._close_settings_popover()
        self.set_visible(False)

    def hide_popup(self) -> None:
        self._hide_popup()

    def _move_and_reveal(self) -> bool:
        self._xmove(self._target_x, self._target_y)
        GLib.timeout_add(60, self._retry_xmove)
        GLib.timeout_add(160, self._retry_xmove)
        GLib.timeout_add(240, self._reveal)
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
            return

        xid = surface.get_xid()
        subprocess.run(['xdotool', 'windowmove', str(xid), str(px), str(py)], capture_output=True)

        if not self._x11_configured:
            surface.set_skip_taskbar_hint(True)
            surface.set_skip_pager_hint(True)
            self._x11_configured = True

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
        box.set_margin_start(4); box.set_margin_end(2)
        box.set_margin_top(4); box.set_margin_bottom(4)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        content_box.set_hexpand(True); content_box.set_valign(Gtk.Align.CENTER)

        if type_ == 'image' and image_path and os.path.exists(image_path):
            pic = Gtk.Picture.new_for_filename(image_path)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_size_request(220, 80)
            pic.set_halign(Gtk.Align.START)
            content_box.append(pic)
        else:
            preview = (content or '[empty]')[:150].replace('\n', ' ').replace('\t', ' ')
            lbl = Gtk.Label(label=preview, halign=Gtk.Align.START, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=45)
            lbl.add_css_class('ch-preview')
            if pinned: lbl.add_css_class('ch-pinned-text')
            content_box.append(lbl)

        ts = datetime.fromtimestamp(created_at).strftime('%H:%M')
        ts_lbl = Gtk.Label(label=ts, halign=Gtk.Align.START); ts_lbl.add_css_class('caption'); ts_lbl.add_css_class('dim-label')
        content_box.append(ts_lbl)
        box.append(content_box)

        # Pin button
        pin_btn = Gtk.Button(icon_name='view-pin-symbolic', tooltip_text='Unpin' if pinned else 'Pin', valign=Gtk.Align.CENTER)
        pin_btn.add_css_class('flat'); pin_btn.add_css_class('circular')
        if pinned: pin_btn.add_css_class('accent')
        pin_btn.connect('clicked', self._on_pin_clicked, row)
        box.append(pin_btn)

        # Delete button
        del_btn = Gtk.Button(icon_name='edit-delete-symbolic', tooltip_text='Delete', valign=Gtk.Align.CENTER)
        del_btn.add_css_class('flat'); del_btn.add_css_class('circular')
        del_btn.connect('clicked', self._on_delete_clicked, row)
        box.append(del_btn)

        row.set_child(box)
        return row

    # ── Filtering ────────────────────────────────────────────────────────────

    def _filter_row(self, row) -> bool:
        query = self._search_entry.get_text().lower().strip()
        return not query or query in getattr(row, '_search_text', '')

    def _on_search_changed(self, entry):
        self._list_box.invalidate_filter()
        row = self._list_box.get_row_at_index(0)
        while row and not row.get_visible(): row = row.get_next_sibling()
        if row: self._list_box.select_row(row)

    def _on_search_activate(self, entry):
        row = self._list_box.get_selected_row()
        if row: self._paste_row(row)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_row_activated(self, list_box, row):
        self._paste_row(row)

    def _paste_row(self, row):
        type_, content, image_path = row._content_type, row._content, row._image_path
        wm_class = self._active_wm_class
        is_terminal = (type_ == 'text' and content and (_is_terminal(wm_class) or _is_vscode_terminal_context(wm_class, self._focus_hints)))
        
        clipboard = Gdk.Display.get_default().get_clipboard()
        if type_ == 'text' and content:
            txt = content.rstrip('\n') if is_terminal else content
            self._monitor.set_skip_hash(hashlib.sha256(txt.encode()).hexdigest())
            clipboard.set(txt); _wl_copy_text(txt)
        elif type_ == 'image' and image_path and os.path.exists(image_path):
            self._monitor.set_skip_hash('image')
            clipboard.set(Gdk.Texture.new_from_filename(image_path)); _wl_copy_image(image_path)

        self._hide_popup()
        key = 'ctrl+shift+v' if is_terminal else _paste_key_for(wm_class)
        GLib.timeout_add(700 if is_terminal else 400, self._do_paste, key)

    def _do_paste(self, key: str) -> bool:
        self._focus_paste_target()
        import uinput_kbd
        if _ON_WAYLAND:
            if uinput_kbd.inject_key(key): return False
            if _HAS_YDOTOOL: subprocess.run(['ydotool', 'key', '--delay', '0', key]); return False
        subprocess.run(['xdotool', 'key', '--clearmodifiers', key])
        return False

    def _focus_paste_target(self) -> None:
        if not self._paste_window_id:
            return
        try:
            popup_xid = None
            surface = self.get_surface()
            if isinstance(surface, GdkX11.X11Surface):
                popup_xid = surface.get_xid()
            if popup_xid and self._paste_window_id == popup_xid:
                return
            subprocess.run(
                ['xdotool', 'windowactivate', '--sync', str(self._paste_window_id)],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except Exception as e:
            print(f'[popup] focus paste target failed: {e}', flush=True)

    def _on_pin_clicked(self, btn, row):
        self._db.toggle_pin(row._entry_id)
        self._populate()

    def _on_delete_clicked(self, btn, row):
        self._db.delete_entry(row._entry_id)
        # Using _populate instead of remove to ensure UI and DB are strictly in sync
        self._populate()

    def _on_clear(self, btn):
        self._close_settings_popover()
        self._dialog_open = True
        dialog = Adw.AlertDialog.new('Clear History?', 'All unpinned clipboard entries will be deleted.')
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('clear', 'Clear')
        dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_clear_confirmed)
        dialog.present(self)

    def _on_clear_confirmed(self, dialog, response):
        self._dialog_open = False
        if response == 'clear':
            self._db.clear_unpinned()
            self._populate()

    # ── Focus / Keyboard ─────────────────────────────────────────────────────

    def _on_active_changed(self, window, pspec):
        if not self.is_active():
            GLib.timeout_add(100, self._check_still_inactive)

    def _check_still_inactive(self) -> bool:
        if self._settings_popover and self._settings_popover.get_visible():
            return False
        if self._dialog_open:
            return False
        if not self.is_active():
            self._hide_popup()
        return False

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        if self._capturing_hotkey:
            if keyval == Gdk.KEY_Escape:
                self._finish_hotkey_capture(None)
                return True
            binding = _event_to_accelerator(keyval, state)
            if binding:
                self._finish_hotkey_capture(binding)
            return True

        if self._search_entry.has_focus() and keyval not in (Gdk.KEY_Escape, Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Delete):
            return False
        if keyval == Gdk.KEY_Escape: self._hide_popup(); return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self._list_box.get_selected_row()
            if row: self._paste_row(row)
            return True
        if keyval == Gdk.KEY_Delete:
            row = self._list_box.get_selected_row()
            if row: self._on_delete_clicked(None, row)
            return True
        if keyval in (Gdk.KEY_p, Gdk.KEY_P):
            row = self._list_box.get_selected_row()
            if row: self._on_pin_clicked(None, row)
            return True
        if keyval == Gdk.KEY_Down: self._move_selection(1); return True
        if keyval == Gdk.KEY_Up: self._move_selection(-1); return True
        return False

    def _move_selection(self, direction: int):
        current = self._list_box.get_selected_row()
        if current is None:
            first = self._list_box.get_row_at_index(0)
            if first: self._list_box.select_row(first)
            return
        idx = current.get_index()
        target = self._list_box.get_row_at_index(idx + direction)
        if target: self._list_box.select_row(target); target.grab_focus()

    def _iter_rows(self):
        row = self._list_box.get_first_child()
        while row:
            yield row
            row = row.get_next_sibling()
