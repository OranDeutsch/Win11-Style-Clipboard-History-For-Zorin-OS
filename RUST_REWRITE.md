# Rust Runtime Notes

The app runtime is now Rust:

- `src/main.rs` starts the GTK application or sends `--show`/`--quit` to the daemon.
- `src/db.rs` keeps the existing SQLite schema and data directory.
- `src/monitor.rs` watches the GTK clipboard and stores copied text/images.
- `src/ui.rs` builds a GTK4 popup using native widgets and symbolic icons so it follows the active Zorin/GTK theme as closely as GTK allows.
- `src/paste.rs` creates a persistent `/dev/uinput` keyboard for paste injection, with `ydotool`/`xdotool` as a fallback.
- `install.sh` now builds the Rust release binary and installs it to `~/.local/bin/clipboard-history`.

GNOME reality:

A true GNOME Shell extension cannot be 100% Rust because GNOME Shell extensions run in GJS. This rewrite is therefore the pure-Rust application path. If exact Windows-style shell overlay behavior becomes mandatory, the practical architecture is a tiny GJS extension bridge plus this Rust daemon.

Current behavior:

- Super+V opens the picker.
- The picker opens centered on the screen instead of chasing caret geometry.
- Search filters entries.
- Arrow keys select entries, and Enter/click pastes the selected entry.
- Pin/delete/clear actions are available.
- The existing database remains compatible.
- The UI avoids custom colors so the current Zorin theme can do the styling.
