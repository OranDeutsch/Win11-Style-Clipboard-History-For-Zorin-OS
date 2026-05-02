#!/bin/bash
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/clipboard-history"
SERVICE_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/clipboard-history"
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CH_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/"

echo "Uninstalling Clipboard History..."

# Stop and disable service
systemctl --user stop clipboard-history 2>/dev/null || true
systemctl --user disable clipboard-history 2>/dev/null || true

# Remove files
rm -f "$BIN_DIR/clipboard-history" "$BIN_DIR/clipboard-history-show"
rm -rf "$LIB_DIR"
rm -f "$SERVICE_DIR/clipboard-history.service"
systemctl --user daemon-reload

# Remove GNOME keybinding
CH_SCHEMA="${MK_SCHEMA}.custom-keybinding:${CH_PATH}"
gsettings reset "$CH_SCHEMA" name    2>/dev/null || true
gsettings reset "$CH_SCHEMA" command 2>/dev/null || true
gsettings reset "$CH_SCHEMA" binding 2>/dev/null || true

CURRENT=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")
FILTERED=$(python3 -c "
import ast, sys
current = '''$CURRENT'''
try:
    items = ast.literal_eval(current)
    items = [i for i in items if 'clipboard-history' not in i]
    print('[' + ', '.join(repr(i) for i in items) + ']')
except:
    print(current)
")
gsettings set "$MK_SCHEMA" custom-keybindings "$FILTERED" 2>/dev/null || true

echo "Clipboard History removed."
echo ""
read -p "Also delete clipboard history database and images? [y/N] " yn
if [[ "${yn,,}" == "y" ]]; then
    rm -rf "$DATA_DIR"
    echo "Data directory removed."
else
    echo "Data kept at: $DATA_DIR"
fi
