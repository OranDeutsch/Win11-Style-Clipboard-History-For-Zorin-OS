"""
Clipboard monitoring and capture logic.

Tracks system clipboard changes, captures text and images, and stores 
them in the database. Includes logic to avoid re-capturing content 
that the daemon itself just pasted.
"""
import hashlib
import time
import uuid
import os
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib

IMAGES_DIR = Path.home() / '.local' / 'share' / 'clipboard-history' / 'images'


class ClipboardMonitor:
    """Watchdog for clipboard changes."""

    def __init__(self, db):
        """Initialize with a reference to the history database."""
        self.db = db
        self._skip_hash: str | None = None  # hash of content set by the daemon
        self._reading = False

    def start(self):
        """Register for clipboard change signals."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        self._clipboard = display.get_clipboard()
        self._clipboard.connect('changed', self._on_changed)

    def set_skip_hash(self, hash_: str):
        """Pre-register a hash to ignore on the next change event (pasting)."""
        self._skip_hash = hash_

    def _on_changed(self, clipboard):
        """Signal handler for clipboard 'changed' event."""
        if clipboard.is_local():
            return
        if self._reading:
            return
        self._reading = True
        # Prioritize text capture
        clipboard.read_text_async(None, self._on_text_ready)

    def _on_text_ready(self, clipboard, result):
        """Callback for text data retrieval."""
        try:
            text = clipboard.read_text_finish(result)
        except Exception:
            text = None

        if text and text.strip():
            h = hashlib.sha256(text.encode()).hexdigest()
            # Verify if this was a self-inflicted change (paste)
            if h != self._skip_hash:
                self.db.add_entry('text', content=text)
            self._skip_hash = None
            self._reading = False
        else:
            # No text or skipped - try image capture
            self._skip_hash = None
            formats = clipboard.get_formats()
            mime_types = list(formats.get_mime_types())
            if any('image' in m for m in mime_types):
                clipboard.read_texture_async(None, self._on_texture_ready)
            else:
                self._reading = False

    def _on_texture_ready(self, clipboard, result):
        """Callback for image texture retrieval."""
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception:
            texture = None
        finally:
            self._reading = False

        if texture is None:
            return

        try:
            # Convert texture to PNG bytes
            png_bytes = texture.save_to_png_bytes()
            raw = bytes(png_bytes.get_data())
            h = hashlib.sha256(raw).hexdigest()
            img_path = IMAGES_DIR / f'{h[:16]}.png'
            
            if not img_path.exists():
                # Save full image first
                texture.save_to_png(str(img_path))
                # Optional: Generate thumbnail to save space/memory in UI
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(str(img_path)) as im:
                        im.thumbnail((480, 320))
                        im.save(str(img_path))
                except Exception:
                    pass  # Fallback to full-res if Pillow is missing
            self.db.add_entry('image', image_path=str(img_path), raw=raw)
        except Exception as e:
            print(f'[monitor] image error: {e}')
