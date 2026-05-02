use crate::db::{hash_bytes, Database};
use gtk::gdk;
use gtk::prelude::*;
use std::cell::{Cell, RefCell};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::rc::Rc;

pub struct ClipboardMonitor {
    clipboard: gdk::Clipboard,
    db: Rc<Database>,
    reading: Cell<bool>,
    skip_hash: RefCell<Option<String>>,
}

impl ClipboardMonitor {
    pub fn start(db: Rc<Database>) -> Option<Rc<Self>> {
        let display = gdk::Display::default()?;
        let clipboard = display.clipboard();
        let monitor = Rc::new(Self {
            clipboard,
            db,
            reading: Cell::new(false),
            skip_hash: RefCell::new(None),
        });

        let weak = Rc::downgrade(&monitor);
        monitor.clipboard.connect_changed(move |_| {
            if let Some(monitor) = weak.upgrade() {
                monitor.on_changed();
            }
        });

        Some(monitor)
    }

    pub fn set_text(&self, text: &str) {
        *self.skip_hash.borrow_mut() = Some(hash_bytes(text.as_bytes()));
        self.clipboard.set_text(text);
        wl_copy("text/plain", text.as_bytes());
    }

    pub fn set_image_from_path(&self, path: &str) -> bool {
        match gdk::Texture::from_filename(path) {
            Ok(texture) => {
                if let Ok(bytes) = fs::read(path) {
                    *self.skip_hash.borrow_mut() = Some(hash_bytes(&bytes));
                    wl_copy("image/png", &bytes);
                }
                self.clipboard.set_texture(&texture);
                true
            }
            Err(error) => {
                eprintln!("[monitor] failed to load image for paste: {error:#}");
                false
            }
        }
    }

    fn on_changed(self: &Rc<Self>) {
        if self.clipboard.is_local() || self.reading.replace(true) {
            return;
        }

        let weak = Rc::downgrade(self);
        self.clipboard
            .read_text_async(None::<&gio::Cancellable>, move |result| {
                if let Some(monitor) = weak.upgrade() {
                    monitor.on_text_ready(result);
                }
            });
    }

    fn on_text_ready(self: &Rc<Self>, result: Result<Option<glib::GString>, glib::Error>) {
        let Ok(Some(text)) = result else {
            *self.skip_hash.borrow_mut() = None;
            self.read_texture();
            return;
        };

        let text = text.to_string();
        if text.trim().is_empty() {
            *self.skip_hash.borrow_mut() = None;
            self.read_texture();
            return;
        }

        let hash = hash_bytes(text.as_bytes());
        if self.skip_hash.borrow().as_deref() != Some(hash.as_str()) {
            if let Err(error) = self.db.add_text(&text) {
                eprintln!("[monitor] failed to store clipboard text: {error:#}");
            }
        }
        *self.skip_hash.borrow_mut() = None;
        self.reading.set(false);
    }

    fn read_texture(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        self.clipboard
            .read_texture_async(None::<&gio::Cancellable>, move |result| {
                if let Some(monitor) = weak.upgrade() {
                    monitor.on_texture_ready(result);
                }
            });
    }

    fn on_texture_ready(self: &Rc<Self>, result: Result<Option<gdk::Texture>, glib::Error>) {
        self.reading.set(false);

        let Ok(Some(texture)) = result else {
            return;
        };

        let png_bytes = texture.save_to_png_bytes();
        let bytes = png_bytes.as_ref();
        let hash = hash_bytes(bytes);
        if self.skip_hash.borrow().as_deref() == Some(hash.as_str()) {
            *self.skip_hash.borrow_mut() = None;
            return;
        }

        let path: PathBuf = self.db.images_dir().join(format!("{}.png", &hash[..16]));
        if !path.exists() {
            if let Err(error) = texture.save_to_png(&path) {
                eprintln!("[monitor] failed to save clipboard image: {error:#}");
                return;
            }
        }

        if let Some(path) = path.to_str() {
            if let Err(error) = self.db.add_image(path, bytes) {
                eprintln!("[monitor] failed to store clipboard image: {error:#}");
            }
        }
        *self.skip_hash.borrow_mut() = None;
    }
}

fn wl_copy(mime_type: &str, bytes: &[u8]) {
    let Ok(mut child) = Command::new("wl-copy")
        .args(["--type", mime_type])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return;
    };

    if let Some(stdin) = child.stdin.as_mut() {
        let _ = stdin.write_all(bytes);
    }
}
