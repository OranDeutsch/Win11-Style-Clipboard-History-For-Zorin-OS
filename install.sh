#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/clipboard-history"
SERVICE_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/clipboard-history"

echo "Installing Clipboard History..."

# ── Directories ──────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR" "$LIB_DIR" "$SERVICE_DIR" "$DATA_DIR/images"

# ── Copy source files ─────────────────────────────────────────────────────────
cp -f "$SCRIPT_DIR/src/"*.py "$LIB_DIR/"
cp -f "$SCRIPT_DIR/src/style.css" "$LIB_DIR/"

# ── Launcher: clipboard-history (daemon) ──────────────────────────────────────
cat > "$BIN_DIR/clipboard-history" << LAUNCHER
#!/bin/bash
exec python3 "$LIB_DIR/main.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/clipboard-history"

# ── Trigger: clipboard-history-show (hotkey target) ───────────────────────────
cat > "$BIN_DIR/clipboard-history-show" << 'TRIGGER'
#!/usr/bin/env python3
import socket, sys, os
path = os.path.expanduser('~/.local/share/clipboard-history/control.sock')
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    s.send(b'SHOW')
    s.close()
except FileNotFoundError:
    print('clipboard-history daemon not running', file=sys.stderr)
    sys.exit(1)
TRIGGER
chmod +x "$BIN_DIR/clipboard-history-show"

# ── Wayland key injection: evdev + ydotool ───────────────────────────────────
# python3-evdev creates a persistent uinput virtual keyboard at daemon start.
# This avoids the ephemeral-device timing race in ydotool standalone mode
# (ydotool creates a new /dev/input device per invocation; Mutter/libinput may
# not register it before the key events fire).  ydotool is kept as a fallback.
_setup_input_injection() {
    local need_udev=0

    # ── python3-evdev (primary) ──────────────────────────────────────────────
    if python3 -c 'import evdev' 2>/dev/null; then
        echo "  python3-evdev: already installed"
    else
        echo "  Installing python3-evdev..."
        if sudo apt-get install -y python3-evdev 2>&1 \
                | grep -v '^Get\|^Fetched\|^Preparing\|^Unpacking\|^Setting'; then
            echo "  python3-evdev: installed"
        else
            echo "  WARNING: python3-evdev install failed — will fall back to ydotool"
        fi
    fi

    # ── ydotool (fallback) ───────────────────────────────────────────────────
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

    # Clean up any leftover ydotoold service (not used in this build).
    systemctl --user disable --now ydotoold 2>/dev/null || true
    rm -f "$SERVICE_DIR/ydotoold.service"
    systemctl --user daemon-reload 2>/dev/null || true

    echo "  Input injection: ready"
}
_setup_input_injection

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

# Remove stale GPaste/CopyQ entries for Super+V to avoid conflicts
for old_path in \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/gpaste/" \
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/copyq/"; do
    old_schema="${MK_SCHEMA}.custom-keybinding:${old_path}"
    old_binding=$(gsettings get "$old_schema" binding 2>/dev/null || echo "''")
    if [[ "$old_binding" == *"Super>v"* ]]; then
        gsettings set "$old_schema" binding "''" 2>/dev/null || true
        echo "  Cleared conflicting Super+V binding from: $old_path"
    fi
done

gsettings set "$CH_SCHEMA" name    "Clipboard History"
gsettings set "$CH_SCHEMA" command "$BIN_DIR/clipboard-history-show"
gsettings set "$CH_SCHEMA" binding "'<Super>v'"

# Add our path to the custom-keybindings list
CURRENT=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")
if [[ "$CURRENT" != *"clipboard-history"* ]]; then
    if [[ "$CURRENT" == "@as []" ]] || [[ "$CURRENT" == "[]" ]]; then
        gsettings set "$MK_SCHEMA" custom-keybindings "['$CH_PATH']"
    else
        NEW="${CURRENT%]}, '$CH_PATH']"
        gsettings set "$MK_SCHEMA" custom-keybindings "$NEW"
    fi
fi

echo ""
echo "Installation complete!"
echo "  Press Super+V to open Clipboard History"
echo "  Press Escape or click outside to close"
echo "  Click an entry to paste it"
echo ""
echo "Useful commands:"
echo "  systemctl --user status clipboard-history   # check daemon"
echo "  journalctl --user -u clipboard-history -f   # live logs"
echo "  $SCRIPT_DIR/uninstall.sh                    # remove"
