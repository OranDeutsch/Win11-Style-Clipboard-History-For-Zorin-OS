# Win11-Style Clipboard History for Zorin OS

A Python/GTK4 clipboard-history picker for Zorin OS (GNOME). It aims to feel close to Windows 11 clipboard history while staying native to the current Zorin/GTK theme.

Press `Super+V` to open the clipboard picker, search previous entries, select with the arrow keys, and paste with `Enter` or a click.

## Features

- **Contextual Positioning**: Opens near your text cursor (caret) when possible.
- **Wayland Support**: Includes experimental support for accurate caret positioning on Wayland/GNOME using IBus and GNOME Introspect.
- **Rich History**: Stores text and image clipboard history in SQLite.
- **Search & Filter**: Quickly find previous clips with an integrated search bar.
- **Pinning**: Keep important clips at the top of the list.
- **Native Aesthetics**: Built with GTK4 and Adwaita to match Zorin OS 17+ styling.
- **Safe Paste**: Uses a persistent virtual keyboard (`uinput`/`evdev`) for reliable paste injection on both X11 and Wayland.

## Install

Run:

```bash
./install.sh
```

The installer will:
- Install required dependencies (`python3-gi`, `python3-evdev`, `ydotool`, etc.).
- Set up udev rules for `/dev/uinput` access.
- Install the `clipboard-history` daemon as a systemd user service.
- Bind `Super+V` to open the picker.

## Usage

- **Super+V**: Open the picker.
- **Up / Down**: Move selection.
- **Enter / Click**: Paste selected entry.
- **Escape / Click Outside**: Close the picker.
- **P**: Toggle pin on the selected item.
- **Delete**: Remove the selected item.

### Wayland Caret Positioning (Experimental)

On Wayland, absolute screen coordinates are restricted. To enable accurate placement near your text cursor:
1. Open the picker (**Super+V**).
2. Click the **Settings** (gear) icon.
3. Toggle **Wayland Caret Positioning (Experimental)**.

This mode uses:
- **IBus**: To capture absolute cursor coordinates from input methods.
- **GNOME Introspect**: To locate the active window's screen position.
- **AT-SPI**: To find the caret position within the focused application.

*Note: For absolute window moving on Wayland, a companion GNOME Shell extension is recommended to grant the application move permissions.*

## Project Structure

- `src/main.py`: Application entry point and daemon lifecycle.
- `src/caret.py`: Fused caret tracking (AT-SPI + IBus + GNOME Introspect).
- `src/popup.py`: GTK4 popup window and UI logic.
- `src/monitor.py`: Clipboard monitoring and SQLite persistence.
- `src/uinput_kbd.py`: Virtual keyboard for paste injection.
- `src/db.py`: Database schema and queries.

## Troubleshooting

- **Check Service Status**: `systemctl --user status clipboard-history`
- **View Logs**: `journalctl --user -u clipboard-history -f`
- **Manual Show**: `clipboard-history --show`

## Uninstall

Run:

```bash
./uninstall.sh
```
