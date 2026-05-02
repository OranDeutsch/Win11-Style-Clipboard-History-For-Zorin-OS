# Win11-Style Clipboard History for Zorin OS

A Rust clipboard-history app for Zorin OS/GNOME that aims to feel close to Windows clipboard history while staying native to the current Zorin/GTK theme.

Press `Super+V` to open a clipboard picker, search previous entries, select with the arrow keys, and paste with `Enter` or a click.

## Current Behavior

- Stores text and image clipboard history in SQLite.
- Opens with `Super+V`.
- Shows a centered GTK popup using native theme colors and controls.
- Supports search, pin, delete, and clear actions.
- Supports arrow-key selection and `Enter` paste.
- Closes with `Escape` or when focus leaves the picker.
- Uses `/dev/uinput` for paste injection, with `ydotool`/`xdotool` fallback.
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
- install and restart the user systemd service,
- bind `Super+V` to the picker,
- disable conflicting GPaste hooks.

## Usage

```bash
clipboard-history --show
clipboard-history --quit
systemctl --user status clipboard-history
journalctl --user -u clipboard-history -f
```

Keyboard controls:

- `Super+V`: open or toggle the picker
- `Up` / `Down`: move selection
- `Home` / `End`: jump to first or last entry
- `Enter`: paste selected entry
- `Escape`: close

## Uninstall

Run:

```bash
./uninstall.sh
```

The uninstaller removes the installed binaries, service, old shell-extension bridge files, and the app keybinding. It asks before deleting the clipboard database and saved images.

## GNOME/Zorin Positioning Note

This is now a Rust GTK app, not a GNOME Shell extension. On GNOME Wayland, normal app windows cannot reliably open at the text caret like the Windows shell popup. The picker therefore opens centered on the screen for predictable behavior.

Exact caret-positioned overlay behavior would require a GNOME Shell extension, and GNOME Shell extensions are written in GJS rather than Rust.

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
- `src/ui.rs`: GTK picker UI
- `src/paste.rs`: paste injection
