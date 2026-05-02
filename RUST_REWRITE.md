# GJS Shell UI + Rust Daemon Notes

The visible picker is now a GNOME Shell extension written in GJS. The Rust binary remains as the native helper daemon for clipboard monitoring, SQLite storage, and paste injection.

- `shell-extension/extension.js` owns the `Super+V` shortcut and renders the history picker inside GNOME Shell.
- `shell-extension/schemas/` stores the Shell keybinding and picker placement setting.
- `src/main.rs` starts the helper daemon or sends CLI commands such as `--list`, `--paste`, `--pin`, `--delete`, and `--clear`.
- `src/app.rs` handles Unix socket commands for the Shell UI.
- `src/db.rs` keeps the existing SQLite schema and data directory.
- `src/monitor.rs` watches the GTK clipboard and stores copied text/images.
- `src/paste.rs` creates a persistent `/dev/uinput` keyboard for paste injection, with `ydotool`/`xdotool` as a fallback.
- `install.sh` builds the Rust release binary, installs the GNOME Shell extension, and enables the Shell keybinding.

GNOME reality:

A true GNOME Shell extension cannot be 100% Rust because GNOME Shell extensions run in GJS. This is now the practical architecture: GJS for the Shell overlay and Rust for native helper work.

Current behavior:

- Super+V opens the picker.
- The picker can open at Center, Mouse, or Window placement from the in-picker settings row.
- Search filters entries.
- Arrow keys select entries, and Enter/click pastes the selected entry.
- Pin/delete/clear actions are available.
- The existing database remains compatible.
- The UI avoids custom colors so the current Zorin theme can do the styling.
