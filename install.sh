#!/bin/bash
# Installation script for Clipboard History (Python/GTK4 version).
#
# Usage:
#   ./install.sh [--hotkey "<Super>v"] [--backend auto|wayland|x11] [--dry-run|--print-actions|--user-only|--yes]
#
# Default hotkey is Super+V.
# Default backend is auto, which lets GTK choose X11 or Wayland from the session.

set -euo pipefail

HOTKEY="<Super>v"
BACKEND="auto"
DRY_RUN=0
PRINT_ACTIONS=0
USER_ONLY=0
ASSUME_YES=0

usage() {
    echo "Usage: $0 [--hotkey \"<Super>v\"] [--backend auto|wayland|x11] [--dry-run|--print-actions|--user-only|--yes]"
}

run_cmd() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf 'DRY RUN:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

confirm_privileged_changes() {
    if [[ "$ASSUME_YES" == "1" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        echo "Refusing privileged changes in non-interactive mode. Re-run with --yes after reviewing --print-actions."
        exit 1
    fi
    echo ""
    echo "This install may use sudo to:"
    if [[ ${#MISSING[@]} -ne 0 ]]; then
        echo "  - install missing packages: ${MISSING[*]}"
    fi
    if [[ "$NEEDS_UINPUT_RULE" == "1" ]]; then
        echo "  - add /etc/udev/rules.d/99-uinput-uaccess.rules for /dev/uinput access"
        echo "  - reload udev rules"
    fi
    read -r -p "Allow these privileged changes? [y/N] " yn
    if [[ "${yn,,}" != "y" ]]; then
        echo "Install cancelled before privileged changes."
        exit 1
    fi
}

print_actions() {
    cat <<EOF
Clipboard History install plan

User-level changes:
  - Create directories:
    $BIN_DIR
    $LIB_DIR
    $SERVICE_DIR
    $DATA_DIR/images
  - Copy Python/GTK app files into:
    $LIB_DIR
  - Create launchers:
    $BIN_DIR/clipboard-history
    $BIN_DIR/clipboard-history-show
  - Install and restart user systemd service:
    $SERVICE_DIR/clipboard-history.service
  - Write GNOME custom keybinding with gsettings:
    Shortcut: $HOTKEY
    Command:  $BIN_DIR/clipboard-history-show
  - GTK backend mode:
    $BACKEND

Privileged/system changes:
  - Missing packages to install with apt: ${MISSING[*]:-(none)}
  - /dev/uinput udev rule needed: $([[ "$NEEDS_UINPUT_RULE" == "1" ]] && echo yes || echo no)

Modes:
  --dry-run      Print commands instead of running them.
  --user-only    Refuse apt/udev changes; install only user-level files/settings.
  --yes          Allow privileged changes without an interactive prompt.
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --hotkey)
            HOTKEY="$2"
            shift 2
            ;;
        --backend)
            BACKEND="$2"
            if [[ "$BACKEND" != "auto" && "$BACKEND" != "wayland" && "$BACKEND" != "x11" ]]; then
                echo "Invalid backend: $BACKEND"
                usage
                exit 1
            fi
            shift 2
            ;;
        --xwayland)
            BACKEND="x11"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --print-actions)
            PRINT_ACTIONS=1
            shift
            ;;
        --user-only)
            USER_ONLY=1
            shift
            ;;
        --yes|-y)
            ASSUME_YES=1
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            usage
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
echo "  GTK Backend: $BACKEND"

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

NEEDS_UINPUT_RULE=0
if [ ! -w /dev/uinput ] && [ ! -f /etc/udev/rules.d/99-uinput-uaccess.rules ]; then
    NEEDS_UINPUT_RULE=1
fi

if [[ "$PRINT_ACTIONS" == "1" || "$DRY_RUN" == "1" ]]; then
    print_actions
    if [[ "$PRINT_ACTIONS" == "1" && "$DRY_RUN" == "0" ]]; then
        exit 0
    fi
fi

if [[ "$USER_ONLY" == "1" && ( ${#MISSING[@]} -ne 0 || "$NEEDS_UINPUT_RULE" == "1" ) ]]; then
    echo "User-only mode requested; skipping privileged dependency/uinput setup."
    if [ ${#MISSING[@]} -ne 0 ]; then
        echo "  Missing packages not installed: ${MISSING[*]}"
    fi
    if [[ "$NEEDS_UINPUT_RULE" == "1" ]]; then
        echo "  /dev/uinput rule not installed. Paste injection may be limited."
    fi
elif [[ ${#MISSING[@]} -ne 0 || "$NEEDS_UINPUT_RULE" == "1" ]]; then
    confirm_privileged_changes
fi

if [ ${#MISSING[@]} -ne 0 ] && [[ "$USER_ONLY" == "0" ]]; then
    echo "  Installing missing packages: ${MISSING[*]}"
    run_cmd sudo apt-get update -qq
    run_cmd sudo apt-get install -y "${MISSING[@]}"
fi

# --- 2. Directory Setup ---
run_cmd mkdir -p "$BIN_DIR" "$LIB_DIR" "$SERVICE_DIR" "$DATA_DIR/images"

# --- 3. Virtual Keyboard (uinput) Setup ---
if [ ! -w /dev/uinput ] && [[ "$USER_ONLY" == "0" ]]; then
    echo "  Configuring /dev/uinput permissions..."
    RULE='/etc/udev/rules.d/99-uinput-uaccess.rules'
    if [ ! -f "$RULE" ]; then
        echo 'KERNEL=="uinput", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
            | if [[ "$DRY_RUN" == "1" ]]; then
                echo "DRY RUN: sudo tee $RULE"
              else
                sudo tee "$RULE" > /dev/null
              fi
        run_cmd sudo udevadm control --reload-rules
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "DRY RUN: sudo udevadm trigger /dev/uinput"
        else
            sudo udevadm trigger /dev/uinput 2>/dev/null || true
        fi
    fi
fi

# --- 4. Copy Application Files ---
echo "  Installing files to $LIB_DIR..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY RUN: cp -f \"$SCRIPT_DIR/src/\"*.py \"$LIB_DIR/\""
else
    cp -f "$SCRIPT_DIR/src/"*.py "$LIB_DIR/"
fi
run_cmd cp -f "$SCRIPT_DIR/src/style.css" "$LIB_DIR/"

# --- 5. Create Launchers ---
if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY RUN: write $BIN_DIR/clipboard-history"
else
    cat > "$BIN_DIR/clipboard-history" << LAUNCHER
#!/bin/bash
exec python3 "$LIB_DIR/main.py" "\$@"
LAUNCHER
fi
run_cmd chmod +x "$BIN_DIR/clipboard-history"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY RUN: write $BIN_DIR/clipboard-history-show"
else
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
fi
run_cmd chmod +x "$BIN_DIR/clipboard-history-show"

# --- 6. Systemd Service ---
echo "  Setting up systemd service..."
run_cmd cp -f "$SCRIPT_DIR/clipboard-history.service" "$SERVICE_DIR/"
if [[ "$BACKEND" == "x11" ]]; then
    run_cmd sed -i '/^Environment=XDG_RUNTIME_DIR=/a Environment=GDK_BACKEND=x11' "$SERVICE_DIR/clipboard-history.service"
elif [[ "$BACKEND" == "wayland" ]]; then
    run_cmd sed -i '/^Environment=XDG_RUNTIME_DIR=/a Environment=GDK_BACKEND=wayland,x11' "$SERVICE_DIR/clipboard-history.service"
fi
run_cmd systemctl --user daemon-reload
run_cmd systemctl --user enable clipboard-history
run_cmd systemctl --user restart clipboard-history

# --- 7. GNOME Keyboard Shortcut ---
echo "  Configuring $HOTKEY shortcut..."
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CH_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/"
CH_SCHEMA="${MK_SCHEMA}.custom-keybinding:${CH_PATH}"

# Clear conflicting shortcuts for the SAME key
# Find any custom keybinding that uses the same hotkey and disable it
# This is better than just hardcoding gpaste/copyq
CUSTOM_BINDINGS=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null | tr -d "[]' " | tr ',' '\n' || true)
for path in $CUSTOM_BINDINGS; do
    if [[ "$path" == "$CH_PATH" ]]; then continue; fi
    schema="${MK_SCHEMA}.custom-keybinding:${path}"
    current_binding=$(gsettings get "$schema" binding 2>/dev/null || echo "''")
    if [[ "$current_binding" == "'$HOTKEY'" ]]; then
        run_cmd gsettings set "$schema" binding "''"
        echo "    Cleared $HOTKEY from existing binding at $path"
    fi
done

run_cmd gsettings set "$CH_SCHEMA" name    "Clipboard History"
run_cmd gsettings set "$CH_SCHEMA" command "$BIN_DIR/clipboard-history-show"
run_cmd gsettings set "$CH_SCHEMA" binding "'$HOTKEY'"

# Register custom binding path if not present
CURRENT=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")
if [[ "$CURRENT" != *"$CH_PATH"* ]]; then
    if [[ "$CURRENT" == "@as []" ]] || [[ "$CURRENT" == "[]" ]]; then
        run_cmd gsettings set "$MK_SCHEMA" custom-keybindings "['$CH_PATH']"
    else
        NEW="${CURRENT%]}, '$CH_PATH']"
        run_cmd gsettings set "$MK_SCHEMA" custom-keybindings "$NEW"
    fi
fi

echo ""
if [[ "$DRY_RUN" == "1" ]]; then
    echo "Dry run complete. No files or settings were changed."
else
    echo "Installation Complete!"
fi
echo "  Hotkey: $HOTKEY"
echo "  GTK Backend: $BACKEND"
if [[ "$DRY_RUN" == "0" ]]; then
    echo "  Daemon: running in background via systemd"
fi
echo ""
echo "To use a different hotkey, run: ./install.sh --hotkey \"<Primary><Shift>v\""
echo "To force a backend, run: ./install.sh --backend wayland  # or x11"
