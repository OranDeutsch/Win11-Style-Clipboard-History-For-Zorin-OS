import hashlib
import time
import uuid
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib

IMAGES_DIR = Path.home() / '.local' / 'share' / 'clipboard-history' / 'images'


class ClipboardMonitor:
    def __init__(self, db):
        self.db = db
        self._skip_hash: str | None = None  # hash of content we set ourselves
        self._reading = False

    def start(self):
        display = Gdk.Display.get_default()
        if display is None:
            return
        self._clipboard = display.get_clipboard()
        self._clipboard.connect('changed', self._on_changed)

    def set_skip_hash(self, hash_: str):
        """Call before setting clipboard content for paste to avoid re-recording it."""
        self._skip_hash = hash_

    def _on_changed(self, clipboard):
        if clipboard.is_local():
            return
        if self._reading:
            return
        self._reading = True
        clipboard.read_text_async(None, self._on_text_ready)

    def _on_text_ready(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except Exception:
            text = None

        if text and text.strip():
            h = hashlib.sha256(text.encode()).hexdigest()
            if h != self._skip_hash:
                self.db.add_entry('text', content=text)
            self._skip_hash = None
            self._reading = False
        else:
            # No text — try image
            self._skip_hash = None
            formats = clipboard.get_formats()
            mime_types = list(formats.get_mime_types())
            if any('image' in m for m in mime_types):
                clipboard.read_texture_async(None, self._on_texture_ready)
            else:
                self._reading = False

    def _on_texture_ready(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception:
            texture = None
        finally:
            self._reading = False

        if texture is None:
            return

        try:
            png_bytes = texture.save_to_png_bytes()
            raw = bytes(png_bytes.get_data())
            h = hashlib.sha256(raw).hexdigest()
            img_path = IMAGES_DIR / f'{h[:16]}.png'
            if not img_path.exists():
                # Save full image first, then shrink to thumbnail on disk
                texture.save_to_png(str(img_path))
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(str(img_path)) as im:
                        im.thumbnail((480, 320))
                        im.save(str(img_path))
                except Exception:
                    pass  # Keep full-res if PIL fails
            self.db.add_entry('image', image_path=str(img_path), raw=raw)
        except Exception as e:
            print(f'[monitor] image error: {e}')
