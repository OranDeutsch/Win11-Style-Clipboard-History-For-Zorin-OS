"""
Persistent virtual keyboard via python-evdev/uinput.

This module creates a single, persistent UInput device at daemon start. 
By keeping the kernel device registered with Mutter/libinput for the daemon's 
entire lifetime, key events arrive reliably without the "ephemeral-device" 
timing race common with tools like ydotool (which create and destroy devices
too quickly for some compositors to register them).
"""
import time

_uinput = None  # evdev.UInput instance, or None if unavailable


def start() -> bool:
    """Create the persistent virtual keyboard.
    
    Requires write access to /dev/uinput (managed by install.sh udev rules).
    Returns True on success.
    """
    global _uinput
    try:
        from evdev import UInput, ecodes as e

        # Define supported keys for paste operations
        caps = {e.EV_KEY: [
            e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
            e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
            e.KEY_LEFTALT, e.KEY_RIGHTALT,
            e.KEY_V, e.KEY_C, e.KEY_X, e.KEY_Z,
        ]}
        
        # Instantiate the virtual device
        _uinput = UInput(caps, name='clipboard-history-kbd', version=0x1)
        dev_path = getattr(getattr(_uinput, 'device', None), 'path', '(unknown)')
        print(f'[uinput_kbd] virtual keyboard created: {dev_path}', flush=True)
        return True
    except ImportError:
        print('[uinput_kbd] python-evdev not installed — will use ydotool/xdotool', flush=True)
        return False
    except PermissionError as ex:
        print(f'[uinput_kbd] /dev/uinput permission denied: {ex} — run install.sh to fix', flush=True)
        return False
    except Exception as ex:
        print(f'[uinput_kbd] start failed: {ex}', flush=True)
        return False


def inject_key(key_str: str) -> bool:
    """
    Inject a key combination chord. 
    
    key_str format: 'ctrl+v' or 'ctrl+shift+v'.
    Returns True on success, False if the virtual keyboard is unavailable.
    """
    if _uinput is None:
        return False

    try:
        from evdev import ecodes as e

        # Mapping table for human-readable key names
        _MAP = {
            'ctrl':    e.KEY_LEFTCTRL,
            'control': e.KEY_LEFTCTRL,
            'shift':   e.KEY_LEFTSHIFT,
            'alt':     e.KEY_LEFTALT,
            'v': e.KEY_V,
            'c': e.KEY_C,
            'x': e.KEY_X,
            'z': e.KEY_Z,
        }

        parts = [p.lower().strip() for p in key_str.split('+')]
        codes = []
        for p in parts:
            if p not in _MAP:
                print(f'[uinput_kbd] unknown key {p!r} in {key_str!r}', flush=True)
                return False
            codes.append(_MAP[p])

        # Sequence: Press all keys, Sync, Wait, Release all in reverse, Sync.
        # This simulates a standard hardware chord.
        for code in codes:
            _uinput.write(e.EV_KEY, code, 1)
        _uinput.syn()

        time.sleep(0.02)  # 20ms hold time ensures recognition by the compositor

        for code in reversed(codes):
            _uinput.write(e.EV_KEY, code, 0)
        _uinput.syn()

        print(f'[uinput_kbd] injected {key_str!r}', flush=True)
        return True
    except Exception as ex:
        print(f'[uinput_kbd] inject_key {key_str!r} failed: {ex}', flush=True)
        return False


def stop() -> None:
    """Safely destroy the virtual keyboard device."""
    global _uinput
    if _uinput:
        try:
            _uinput.close()
        except Exception:
            pass
        _uinput = None
