#!/bin/bash
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/clipboard-history"
SERVICE_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/.local/share/clipboard-history"
EXT_UUID="clipboard-history-rust@missionzero.dev"
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CH_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/clipboard-history/"

echo "Uninstalling Clipboard History..."

# Stop and disable service
systemctl --user stop clipboard-history 2>/dev/null || true
systemctl --user disable clipboard-history 2>/dev/null || true

# Remove files
gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
gnome-extensions disable "clipboard-history-gjs@missionzero.dev" 2>/dev/null || true
gnome-extensions disable "clipboard-history-rust@missionzero" 2>/dev/null || true
rm -f "$BIN_DIR/clipboard-history" "$BIN_DIR/clipboard-history-show"
rm -rf "$LIB_DIR"
rm -rf "$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
rm -rf "$HOME/.local/share/gnome-shell/extensions/clipboard-history-gjs@missionzero.dev"
rm -rf "$HOME/.local/share/gnome-shell/extensions/clipboard-history-rust@missionzero"
rm -f "$SERVICE_DIR/clipboard-history.service"
systemctl --user daemon-reload

# Remove GNOME keybinding
CH_SCHEMA="${MK_SCHEMA}.custom-keybinding:${CH_PATH}"
gsettings reset "$CH_SCHEMA" name    2>/dev/null || true
gsettings reset "$CH_SCHEMA" command 2>/dev/null || true
gsettings reset "$CH_SCHEMA" binding 2>/dev/null || true

CURRENT=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")
if [[ "$CURRENT" == "['$CH_PATH']" ]]; then
    gsettings set "$MK_SCHEMA" custom-keybindings "[]" 2>/dev/null || true
else
    echo "Leaving custom keybinding list unchanged because it contains other entries."
fi

echo "Clipboard History removed."
echo ""
read -p "Also delete clipboard history database and images? [y/N] " yn
if [[ "${yn,,}" == "y" ]]; then
    rm -rf "$DATA_DIR"
    echo "Data directory removed."
else
    echo "Data kept at: $DATA_DIR"
fi
