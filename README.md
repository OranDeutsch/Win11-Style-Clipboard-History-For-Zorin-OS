# Win11-Style Clipboard History for Zorin OS

A Python/GTK4 clipboard-history picker for Zorin OS (GNOME). It aims to feel close to Windows 11 clipboard history while staying native to the current Zorin/GTK theme.

Press `Super+V` to open the clipboard picker, search previous entries, select with the arrow keys, and paste with `Enter` or a click.

## Preview

![Clipboard History picker showing text and image entries](Media/Screenshot%20from%202026-05-04%2001-31-32.png)

![Clipboard History settings and positioning options](Media/Screenshot%20from%202026-05-04%2001-39-58.png)

![Clipboard History shortcut capture dialog](Media/Screenshot%20from%202026-05-04%2001-40-11.png)

<video src="Media/Screencast%20from%202026-05-04%2001-44-11.webm" controls width="720">
  Demo video: Media/Screencast from 2026-05-04 01-44-11.webm
</video>

## TL;DR Warning

This is a desktop integration tool, not a sandboxed app. The installer adds a user systemd service, writes GNOME keyboard-shortcut settings with `gsettings`, installs files under `~/.local`, and may configure `/dev/uinput` access so the app can paste into other windows. Read `install.sh` first if you are unsure, and do not install it on a system where you are not comfortable with those changes.

## Features

- **Contextual Positioning**: Choose between **Caret** (near text cursor), **Mouse**, **Window**, or **OS Default** placement.
- **X11 and Wayland Support**: Runs on either GTK backend. X11/XWayland supports explicit popup movement; native Wayland uses compositor placement with caret/window context as best-effort metadata.
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

To inspect what the installer will do before it changes anything:

```bash
./install.sh --print-actions
./install.sh --dry-run
```

To avoid privileged package/udev changes and install only user-level files and settings:

```bash
./install.sh --user-only
```

By default the installer uses `--backend auto`, which lets GTK choose the best backend for the current session. You can force one:

```bash
./install.sh --backend wayland
./install.sh --backend x11
```

Use native Wayland for the most session-native behavior. Use `--backend x11` if you want XWayland's more precise popup movement and mouse-window targeting.

### Custom Hotkey

By default, the picker is bound to `Super+V`. To use a different hotkey during install, pass the `--hotkey` argument:

```bash
./install.sh --hotkey "<Primary><Shift>v"
```

The installer will:
- Install required dependencies (`python3-gi`, `python3-evdev`, `ydotool`, etc.).
- Set up udev rules for `/dev/uinput` access.
- Install the `clipboard-history` daemon as a systemd user service.
- Bind your chosen hotkey to open the picker.

If dependencies or `/dev/uinput` access require `sudo`, the installer prints the privileged actions and asks for confirmation first. Use `--yes` only after reviewing `--print-actions`.

You can also change the hotkey live from the app:

1. Open the picker.
2. Click the **Settings** (gear) icon.
3. Click **Set Shortcut** and press your new key combination.

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
   - **Keyboard Shortcut**: Change the global hotkey live without reinstalling.
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
- **Backend Choice**: rerun `./install.sh --backend auto`, `./install.sh --backend wayland`, or `./install.sh --backend x11`.

## Uninstall

Run:

```bash
./uninstall.sh
```

## Security & Permissions

Clipboard History needs desktop integration that ordinary sandboxed apps usually cannot access. It monitors clipboard changes, registers a GNOME global shortcut, and uses a virtual keyboard device for reliable paste injection.

The safer install modes are:

- `./install.sh --print-actions`: show the full install plan and exit.
- `./install.sh --dry-run`: print commands instead of running them.
- `./install.sh --user-only`: skip `sudo`, apt dependency installation, and udev setup.
- `./install.sh --yes`: allow privileged changes non-interactively after you have reviewed the plan.

The app stores data under `~/.local/share/clipboard-history`, installs launchers under `~/.local/bin`, installs app files under `~/.local/lib/clipboard-history`, and registers a user service under `~/.config/systemd/user`.

The systemd user service also enables conservative hardening such as `NoNewPrivileges`, private temp storage, read-only system paths, and kernel/control-group protections. It does not use a strict device sandbox because reliable paste injection needs access to `/dev/uinput`.
