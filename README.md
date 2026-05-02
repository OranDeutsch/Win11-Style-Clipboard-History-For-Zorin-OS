# Win11-Style Clipboard History for Zorin OS

A GNOME Shell/GJS clipboard-history picker for Zorin OS backed by a small Rust daemon. It aims to feel close to Windows clipboard history while staying native to the current Zorin Shell theme.

Press `Super+V` to open a clipboard picker, search previous entries, select with the arrow keys, and paste with `Enter` or a click.

## Current Behavior

- Stores text and image clipboard history in SQLite.
- Opens with `Super+V`.
- Shows a GNOME Shell popup using the active Zorin/GNOME Shell theme.
- Includes an `Open at` selector in the history picker for `Center`, `Mouse`, or `Window` placement.
- Supports search, pin, delete, and clear actions.
- Supports arrow-key selection and `Enter` paste.
- Closes with `Escape` or when focus leaves the picker.
- Uses the Rust daemon for clipboard monitoring, SQLite storage, and `/dev/uinput` paste injection.
- Disables GPaste during install so it does not steal `Super+V` or D-Bus clipboard events.

## Install

Run:

```bash
./install.sh
```

The installer will:

- install missing build/runtime packages where possible,
- build the Rust release binary,
- install `clipboard-history` and `clipboard-history-show` into `~/.local/bin`,
- install and enable the GNOME Shell extension,
- install and restart the user systemd service,
- bind `Super+V` inside GNOME Shell,
- disable conflicting GPaste hooks.

## Usage

```bash
clipboard-history --show
clipboard-history --list
clipboard-history --paste ENTRY_ID
clipboard-history --quit
systemctl --user status clipboard-history
journalctl --user -u clipboard-history -f
```

Keyboard controls:

- `Super+V`: open or toggle the Shell picker
- `Up` / `Down`: move selection
- `Home` / `End`: jump to first or last entry
- `Enter`: paste selected entry
- `Escape`: close

## Uninstall

Run:

```bash
./uninstall.sh
```

The uninstaller removes the installed binaries, service, Shell extension, old shell-extension bridge files, and the app keybinding. It asks before deleting the clipboard database and saved images.

## GNOME/Zorin Positioning Note

The visible picker is now a GNOME Shell extension written in GJS. Use the `Open at` selector in the picker to choose `Center`, `Mouse`, or `Window` placement.

GNOME Shell still does not expose a reliable universal text-caret rectangle for every application on Wayland. `Mouse` or `Window` placement are the practical closest options for Windows-style insertion context.

## Development

```bash
cargo check
cargo build --release
./clipboard-history
./clipboard-history-show
```

Project layout:

- `src/main.rs`: app startup and socket command client
- `src/app.rs`: daemon state and Unix socket command handling
- `src/db.rs`: SQLite storage
- `src/monitor.rs`: clipboard monitoring
- `src/paste.rs`: paste injection
- `shell-extension/extension.js`: GNOME Shell/GJS picker UI
- `shell-extension/schemas/`: Shell shortcut and placement settings
