"""
Clipboard History Daemon.

This is the primary background process that monitors the clipboard, 
maintains the SQLite database, and presents the history picker UI via 
Unix socket commands.
"""
import os
import sys
import socket
import signal
from pathlib import Path

# Do NOT disable accessibility; we need AT-SPI for caret tracking.
# Also clear stale AT-SPI socket paths inherited by systemd.
os.environ.pop('AT_SPI_BUS_ADDRESS', None)

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Adw, GLib, Gio

sys.path.insert(0, str(Path(__file__).parent))
from db import Database
from monitor import ClipboardMonitor
from popup import ClipboardPopup
import caret as caret_mod
import uinput_kbd

SOCKET_PATH = Path.home() / '.local' / 'share' / 'clipboard-history' / 'control.sock'
APP_ID = 'tech.missionzero.clipboard-history'


class ClipboardHistoryApp(Adw.Application):
    """GTK Application class managing the daemon state."""

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self._db: Database | None = None
        self._monitor: ClipboardMonitor | None = None
        self._popup: ClipboardPopup | None = None
        self._socket_server: socket.socket | None = None

    # --- Lifecycle ---

    def do_activate(self):
        """Called when the application starts or is re-activated."""
        self._db = Database()
        self._monitor = ClipboardMonitor(self._db)
        self._monitor.start()

        # AT-SPI caret tracking for popup positioning near text cursor
        caret_mod.start()

        # Persistent virtual keyboard for Wayland paste injection.
        uinput_kbd.start()

        self.hold()  # Prevent exit when all windows close
        self._start_socket_server()
        
        # Handle termination signals gracefully
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)
        print('[clipboard-history] daemon started', flush=True)

    def _on_sigterm(self):
        """Handle SIGTERM to ensure clean shutdown."""
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        """Clean up resources on application exit."""
        self._stop_socket_server()
        uinput_kbd.stop()
        if self._db:
            self._db.close()
        Adw.Application.do_shutdown(self)

    # --- Popup UI ---

    def show_popup(self) -> bool:
        """Trigger the history picker visibility."""
        if self._db is None or self._monitor is None:
            return GLib.SOURCE_REMOVE

        # Toggle visibility if already open
        if self._popup and self._popup.get_visible():
            self._popup.hide_popup()
            return GLib.SOURCE_REMOVE

        if self._popup is None:
            self._popup = ClipboardPopup(self, self._db, self._monitor,
                                         caret_tracker=caret_mod)

        self._popup.show_at_best_position()
        return GLib.SOURCE_REMOVE

    # --- Unix Socket Server ---

    def _start_socket_server(self):
        """Create a local socket to listen for 'SHOW' or 'QUIT' commands."""
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(SOCKET_PATH))
        server.listen(5)
        server.setblocking(False)
        self._socket_server = server

        # Register the socket with GLib's main loop
        GLib.io_add_watch(
            server.fileno(),
            GLib.IOCondition.IN,
            self._on_socket_ready,
            server
        )

    def _on_socket_ready(self, fd, condition, server) -> bool:
        """Callback when the socket has data waiting to be read."""
        try:
            conn, _ = server.accept()
            data = conn.recv(32).decode(errors='ignore').strip()
            conn.close()
            if data == 'SHOW':
                GLib.idle_add(self.show_popup)
            elif data == 'QUIT':
                self.quit()
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE

    def _stop_socket_server(self):
        """Close and remove the Unix socket."""
        if self._socket_server:
            try: self._socket_server.close()
            except Exception: pass
        if SOCKET_PATH.exists():
            try: SOCKET_PATH.unlink()
            except Exception: pass


def send_command(cmd: str):
    """Client utility to send a single command to the running daemon."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(SOCKET_PATH))
        s.send(cmd.encode())
        s.close()
    except FileNotFoundError:
        print('[clipboard-history] daemon not running', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f'[clipboard-history] error: {e}', file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point for both daemon and command-line triggers."""
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == '--show':
            send_command('SHOW'); return
        if cmd == '--quit':
            send_command('QUIT'); return

    app = ClipboardHistoryApp()
    sys.exit(app.run(sys.argv[:1]))


if __name__ == '__main__':
    main()
