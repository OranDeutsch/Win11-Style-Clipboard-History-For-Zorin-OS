# Win11-Style Clipboard History for Zorin OS

A Python/GTK4 clipboard-history picker for Zorin OS (GNOME). It aims to feel close to Windows 11 clipboard history while staying native to the current Zorin/GTK theme.

Press `Super+V` to open the clipboard picker, search previous entries, select with the arrow keys, and paste with `Enter` or a click.

## Features

- **Contextual Positioning**: Choose between **Caret** (near text cursor), **Mouse**, **Window**, or **OS Default** placement.
- **Wayland Support**: Includes experimental support for accurate caret positioning on Wayland/GNOME using IBus and GNOME Introspect.
- **Rich History**: Stores text and image clipboard history in SQLite.
- **Search & Filter**: Quickly find previous clips with an integrated search bar.
- **Pinning**: Keep important clips at the top of the list.
- **Delete & Clear**: Easily remove individual entries, clear unpinned history, or wipe everything from the Settings menu.
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

### Settings & Customization

1. Open the picker (**Super+V**).
2. Click the **Settings** (gear) icon.
3. Configure your preferences:
   - **Positioning Mode**: Explicitly set the popup to follow your **Caret**, **Mouse**, **Active Window**, or use the **OS Default**.
   - **Wayland Caret Positioning (Experimental)**: Enable absolute coordinate tracking on Wayland/GNOME.
   - **Delete All History**: Completely wipe the database, including pinned items.

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
