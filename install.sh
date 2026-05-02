#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/clipboard-history"
SERVICE_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/clipboard-history"
EXT_UUID="clipboard-history-rust@missionzero.dev"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

echo "Installing Clipboard History..."

# ── Directories ──────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR" "$LIB_DIR" "$SERVICE_DIR" "$DATA_DIR/images"

# ── Rust/GTK build dependencies ──────────────────────────────────────────────
_install_build_deps() {
    local missing=()
    command -v cargo >/dev/null 2>&1 || missing+=(cargo rustc)
    command -v pkg-config >/dev/null 2>&1 || missing+=(pkg-config)
    pkg-config --exists gtk4 2>/dev/null || missing+=(libgtk-4-dev)
    pkg-config --exists x11 2>/dev/null || missing+=(libx11-dev)
    command -v cc >/dev/null 2>&1 || missing+=(build-essential)
    command -v glib-compile-schemas >/dev/null 2>&1 || missing+=(libglib2.0-bin)
    command -v wl-copy >/dev/null 2>&1 || missing+=(wl-clipboard)

    if (( ${#missing[@]} > 0 )); then
        echo "  Installing build dependencies: ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    fi
}
_install_build_deps

# ── Build and install Rust binary ─────────────────────────────────────────────
cargo build --release --manifest-path "$SCRIPT_DIR/Cargo.toml"
install -m 0755 "$SCRIPT_DIR/target/release/clipboard-history" "$BIN_DIR/clipboard-history"
install -m 0755 "$SCRIPT_DIR/gjs/clipboard-history-picker.js" "$LIB_DIR/clipboard-history-picker.js"
cat > "$BIN_DIR/clipboard-history-show" << LAUNCHER
#!/bin/bash
GDK_BACKEND=x11 exec gjs "$LIB_DIR/clipboard-history-picker.js"
LAUNCHER
chmod +x "$BIN_DIR/clipboard-history-show"

# ── GNOME Shell extension UI ─────────────────────────────────────────────────
rm -rf "$EXT_DIR"
mkdir -p "$EXT_DIR/schemas"
cp -f "$SCRIPT_DIR/shell-extension/metadata.json" "$EXT_DIR/"
cp -f "$SCRIPT_DIR/shell-extension/extension.js" "$EXT_DIR/"
cp -f "$SCRIPT_DIR/shell-extension/stylesheet.css" "$EXT_DIR/"
cp -f "$SCRIPT_DIR/shell-extension/schemas/"*.xml "$EXT_DIR/schemas/"
glib-compile-schemas "$EXT_DIR/schemas"

# ── Wayland key injection: native uinput + ydotool fallback ───────────────────
_setup_input_injection() {
    if ! command -v ydotool &>/dev/null; then
        echo "  Installing ydotool..."
        if ! sudo apt-get install -y ydotool 2>&1 \
                | grep -v '^Get\|^Fetched\|^Preparing\|^Unpacking\|^Setting'; then
            echo "  WARNING: ydotool install failed"
        fi
    fi

    # ── /dev/uinput access (needed by both evdev and ydotool) ────────────────
    if [ ! -w /dev/uinput ]; then
        local RULE='/etc/udev/rules.d/99-uinput-uaccess.rules'
        if [ ! -f "$RULE" ]; then
            echo "  Adding udev rule for /dev/uinput access..."
            echo 'KERNEL=="uinput", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
                | sudo tee "$RULE" > /dev/null
            sudo udevadm control --reload-rules
            sudo udevadm trigger /dev/uinput 2>/dev/null || true
        fi
    fi

    # Clean up any leftover ydotoold service. The Rust daemon keeps its own
    # persistent uinput keyboard and only shells out to ydotool as a fallback.
    systemctl --user disable --now ydotoold 2>/dev/null || true
    rm -f "$SERVICE_DIR/ydotoold.service"
    systemctl --user daemon-reload 2>/dev/null || true

    echo "  Input injection: ready"
}
_setup_input_injection

# ── Disable GPaste (conflicts via D-Bus activation) ──────────────────────────
_disable_gpaste() {
    # Stop tracking and kill the running daemon
    gsettings set org.gnome.GPaste track-changes false 2>/dev/null || true
    pkill -x gpaste-daemon 2>/dev/null || true

    # Block D-Bus auto-activation by shadowing the session-bus service files
    # with stubs that exec /bin/false. User files in ~/.local/share/dbus-1/services/
    # take precedence over /usr/share/dbus-1/services/ for the session bus.
    local dbus_user_svc="$HOME/.local/share/dbus-1/services"
    mkdir -p "$dbus_user_svc"
    for name in org.gnome.GPaste org.gnome.GPaste.Ui org.gnome.GPaste.Preferences; do
        printf '[D-BUS Service]\nName=%s\nExec=/bin/false\n' "$name" \
            > "$dbus_user_svc/$name.service"
    done

    echo "  GPaste: disabled"
}
_disable_gpaste

# ── systemd service ───────────────────────────────────────────────────────────
cp -f "$SCRIPT_DIR/clipboard-history.service" "$SERVICE_DIR/"
systemctl --user daemon-reload
systemctl --user enable clipboard-history
systemctl --user restart clipboard-history

echo "Waiting for daemon to start..."
sleep 2
if systemctl --user is-active --quiet clipboard-history; then
    echo "  Daemon: running"
else
    echo "  WARNING: daemon may not have started. Check: systemctl --user status clipboard-history"
fi

# ── GNOME keyboard shortcut (Super+V) ────────────────────────────────────────
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CH_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/"
CH_SCHEMA="${MK_SCHEMA}.custom-keybinding:${CH_PATH}"

# Remove stale GPaste/CopyQ entries to avoid conflicts or dead shortcuts.
for old_path in \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/gpaste/" \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/copyq/"; do
    old_schema="${MK_SCHEMA}.custom-keybinding:${old_path}"
    gsettings set "$old_schema" binding "''" 2>/dev/null || true
done

gsettings set "$CH_SCHEMA" name    "Clipboard History"
gsettings set "$CH_SCHEMA" command "$BIN_DIR/clipboard-history-show"
gsettings set "$CH_SCHEMA" binding "'<Super>v'"

gsettings set "$MK_SCHEMA" custom-keybindings "['$CH_PATH']"

echo ""
if command -v gnome-extensions >/dev/null 2>&1; then
    # Keep conflicting clipboard extensions from stealing Super+V.
    gnome-extensions disable "GPaste@gnome-shell-extensions.gnome.org" 2>/dev/null || true
    gnome-extensions disable "clipboard-history-rust@missionzero" 2>/dev/null || true
    gnome-extensions disable "clipboard-history-gjs@missionzero.dev" 2>/dev/null || true
    # The Shell extension is installed for the next Shell/session reload, but
    # Super+V uses the GJS launcher immediately so updates do not require logout.
    gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
fi

echo "Installation complete!"
echo "  Press Super+V to open Clipboard History"
echo "  Press Escape or click outside to close"
echo "  Click an entry to paste it"
echo ""
echo "Useful commands:"
echo "  systemctl --user status clipboard-history   # check daemon"
echo "  journalctl --user -u clipboard-history -f   # live logs"
echo "  $SCRIPT_DIR/uninstall.sh                    # remove"
