#!/bin/bash
# Installation script for Clipboard History (Python/GTK4 version).
#
# Usage:
#   ./install.sh [--hotkey "<Super>v"]
#
# Default hotkey is Super+V.

set -euo pipefail

HOTKEY="<Super>v"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --hotkey)
            HOTKEY="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--hotkey \"<Super>v\"]"
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/clipboard-history"
SERVICE_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/clipboard-history"

echo "Installing Clipboard History..."
echo "  Target Hotkey: $HOTKEY"

# --- 1. System Dependencies ---
echo "Checking dependencies..."
DEPS=(
    "python3-gi" 
    "python3-gi-cairo" 
    "gir1.2-gtk-4.0" 
    "gir1.2-adw-1" 
    "python3-evdev" 
    "python3-pil" 
    "xdotool" 
    "ydotool" 
    "wl-clipboard"
)

MISSING=()
for dep in "${DEPS[@]}"; do
    if ! dpkg -s "$dep" >/dev/null 2>&1; then
        MISSING+=("$dep")
    fi
done

if [ ${#MISSING[@]} -ne 0 ]; then
    echo "  Installing missing packages: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING[@]}" >/dev/null
fi

# --- 2. Directory Setup ---
mkdir -p "$BIN_DIR" "$LIB_DIR" "$SERVICE_DIR" "$DATA_DIR/images"

# --- 3. Virtual Keyboard (uinput) Setup ---
if [ ! -w /dev/uinput ]; then
    echo "  Configuring /dev/uinput permissions..."
    RULE='/etc/udev/rules.d/99-uinput-uaccess.rules'
    if [ ! -f "$RULE" ]; then
        echo 'KERNEL=="uinput", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
            | sudo tee "$RULE" > /dev/null
        sudo udevadm control --reload-rules
        sudo udevadm trigger /dev/uinput 2>/dev/null || true
    fi
fi

# --- 4. Copy Application Files ---
echo "  Installing files to $LIB_DIR..."
cp -f "$SCRIPT_DIR/src/"*.py "$LIB_DIR/"
cp -f "$SCRIPT_DIR/src/style.css" "$LIB_DIR/"

# --- 5. Create Launchers ---
cat > "$BIN_DIR/clipboard-history" << LAUNCHER
#!/bin/bash
exec python3 "$LIB_DIR/main.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/clipboard-history"

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
except Exception as e:
    print(f'Error connecting to daemon: {e}', file=sys.stderr)
    sys.exit(1)
TRIGGER
chmod +x "$BIN_DIR/clipboard-history-show"

# --- 6. Systemd Service ---
echo "  Setting up systemd service..."
cp -f "$SCRIPT_DIR/clipboard-history.service" "$SERVICE_DIR/"
systemctl --user daemon-reload
systemctl --user enable clipboard-history
systemctl --user restart clipboard-history

# --- 7. GNOME Keyboard Shortcut ---
echo "  Configuring $HOTKEY shortcut..."
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CH_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/"
CH_SCHEMA="${MK_SCHEMA}.custom-keybinding:${CH_PATH}"

# Clear conflicting shortcuts for the SAME key
# Find any custom keybinding that uses the same hotkey and disable it
# This is better than just hardcoding gpaste/copyq
CUSTOM_BINDINGS=$(gsettings get "$MK_SCHEMA" custom-keybindings | tr -d "[]' " | tr ',' '\n')
for path in $CUSTOM_BINDINGS; do
    if [[ "$path" == "$CH_PATH" ]]; then continue; fi
    schema="${MK_SCHEMA}.custom-keybinding:${path}"
    current_binding=$(gsettings get "$schema" binding 2>/dev/null || echo "''")
    if [[ "$current_binding" == "'$HOTKEY'" ]]; then
        gsettings set "$schema" binding "''"
        echo "    Cleared $HOTKEY from existing binding at $path"
    fi
done

gsettings set "$CH_SCHEMA" name    "Clipboard History"
gsettings set "$CH_SCHEMA" command "$BIN_DIR/clipboard-history-show"
gsettings set "$CH_SCHEMA" binding "'$HOTKEY'"

# Register custom binding path if not present
CURRENT=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")
if [[ "$CURRENT" != *"$CH_PATH"* ]]; then
    if [[ "$CURRENT" == "@as []" ]] || [[ "$CURRENT" == "[]" ]]; then
        gsettings set "$MK_SCHEMA" custom-keybindings "['$CH_PATH']"
    else
        NEW="${CURRENT%]}, '$CH_PATH']"
        gsettings set "$MK_SCHEMA" custom-keybindings "$NEW"
    fi
fi

echo ""
echo "Installation Complete!"
echo "  Hotkey: $HOTKEY"
echo "  Daemon: running in background via systemd"
echo ""
echo "To use a different hotkey, run: ./install.sh --hotkey \"<Primary><Shift>v\""
