use crate::db::{hash_bytes, Database, Entry};
use crate::monitor::ClipboardMonitor;
use crate::paste::Keyboard;
use anyhow::Result;
use chrono::{DateTime, Local};
use gtk::gdk;
use gtk::prelude::*;
use std::cell::RefCell;
use std::path::Path;
use std::process::Command;
use std::rc::Rc;
use std::time::{Duration, Instant, UNIX_EPOCH};

const POPUP_WIDTH: i32 = 580;
const POPUP_HEIGHT: i32 = 720;

#[derive(Clone, Debug, Default)]
struct PasteTarget {
    window_id: Option<String>,
    wm_class: String,
}

impl PasteTarget {
    fn is_terminal(&self) -> bool {
        is_terminal_class(&self.wm_class)
    }
}

pub struct ClipboardPopup {
    window: gtk::ApplicationWindow,
    search: gtk::SearchEntry,
    list: gtk::ListBox,
    empty: gtk::Label,
    db: Rc<Database>,
    monitor: Rc<ClipboardMonitor>,
    keyboard: Rc<Keyboard>,
    rows: RefCell<Vec<Entry>>,
    paste_target: RefCell<PasteTarget>,
    ignore_focus_loss_until: RefCell<Option<Instant>>,
}

impl ClipboardPopup {
    pub fn new(
        app: &gtk::Application,
        db: Rc<Database>,
        monitor: Rc<ClipboardMonitor>,
        keyboard: Rc<Keyboard>,
    ) -> Rc<Self> {
        let window = gtk::ApplicationWindow::builder()
            .application(app)
            .title("Clipboard History")
            .default_width(POPUP_WIDTH)
            .default_height(POPUP_HEIGHT)
            .hide_on_close(true)
            .resizable(false)
            .build();

        let header = gtk::HeaderBar::new();
        let search = gtk::SearchEntry::new();
        search.set_hexpand(true);
        header.set_title_widget(Some(&search));

        let clear = gtk::Button::from_icon_name("edit-clear-all-symbolic");
        clear.set_tooltip_text(Some("Clear unpinned history"));
        clear.add_css_class("flat");
        header.pack_end(&clear);
        window.set_titlebar(Some(&header));

        let scrolled = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .vscrollbar_policy(gtk::PolicyType::Automatic)
            .vexpand(true)
            .build();

        let list = gtk::ListBox::new();
        list.set_selection_mode(gtk::SelectionMode::Browse);
        list.set_activate_on_single_click(true);
        list.add_css_class("boxed-list");
        list.set_margin_top(6);
        list.set_margin_bottom(6);
        list.set_margin_start(8);
        list.set_margin_end(8);
        scrolled.set_child(Some(&list));

        let empty = gtk::Label::new(Some("No clipboard history yet.\nCopy something to get started."));
        empty.set_justify(gtk::Justification::Center);
        empty.add_css_class("dim-label");
        empty.set_margin_top(40);
        empty.set_margin_bottom(40);
        empty.set_visible(false);

        let outer = gtk::Box::new(gtk::Orientation::Vertical, 0);
        outer.append(&scrolled);
        outer.append(&empty);
        window.set_child(Some(&outer));

        let popup = Rc::new(Self {
            window,
            search,
            list,
            empty,
            db,
            monitor,
            keyboard,
            rows: RefCell::new(Vec::new()),
            paste_target: RefCell::new(PasteTarget::default()),
            ignore_focus_loss_until: RefCell::new(None),
        });

        popup.connect(clear);
        popup
    }

    pub fn toggle(self: &Rc<Self>) {
        if self.window.is_visible() {
            eprintln!("[popup] hiding");
            self.window.set_visible(false);
            return;
        }

        eprintln!("[popup] showing");
        let target = current_paste_target();
        eprintln!(
            "[popup] target wm_class={:?} xid={:?}",
            target.wm_class, target.window_id
        );
        *self.paste_target.borrow_mut() = target.clone();

        if let Err(error) = self.populate() {
            eprintln!("[popup] populate failed: {error:#}");
        }

        *self.ignore_focus_loss_until.borrow_mut() =
            Some(Instant::now() + Duration::from_millis(450));
        self.window.set_opacity(0.0);
        self.window.present();
        eprintln!("[popup] presented transparent visible={}", self.window.is_visible());
        self.search.grab_focus();
        if let Some(row) = self.list.row_at_index(0) {
            self.list.select_row(Some(&row));
        }

        let weak = Rc::downgrade(self);
        glib::timeout_add_local_once(Duration::from_millis(20), move || {
            if let Some(popup) = weak.upgrade() {
                popup.move_to_screen_center();
                let weak = Rc::downgrade(&popup);
                glib::timeout_add_local_once(Duration::from_millis(30), move || {
                    if let Some(popup) = weak.upgrade() {
                        popup.window.set_opacity(1.0);
                        eprintln!("[popup] revealed");
                    }
                });
            }
        });
    }

    fn connect(self: &Rc<Self>, clear: gtk::Button) {
        let weak = Rc::downgrade(self);
        self.window.connect_is_active_notify(move |window| {
            if let Some(popup) = weak.upgrade() {
                if popup.window.is_visible() && !window.is_active() {
                    if popup
                        .ignore_focus_loss_until
                        .borrow()
                        .is_some_and(|deadline| Instant::now() < deadline)
                    {
                        eprintln!("[popup] ignoring initial focus loss");
                        return;
                    }
                    eprintln!("[popup] hiding on focus loss");
                    popup.window.set_visible(false);
                }
            }
        });

        let weak = Rc::downgrade(self);
        clear.connect_clicked(move |_| {
            if let Some(popup) = weak.upgrade() {
                if let Err(error) = popup.db.clear_unpinned() {
                    eprintln!("[popup] clear failed: {error:#}");
                }
                let _ = popup.populate();
            }
        });

        let weak = Rc::downgrade(self);
        self.search.connect_search_changed(move |_| {
            if let Some(popup) = weak.upgrade() {
                let _ = popup.populate();
            }
        });

        let weak = Rc::downgrade(self);
        self.search.connect_activate(move |_| {
            if let Some(popup) = weak.upgrade() {
                if let Some(row) = popup.list.selected_row() {
                    popup.paste_row(row.index());
                }
            }
        });

        let weak = Rc::downgrade(self);
        self.list.connect_row_activated(move |_, row| {
            if let Some(popup) = weak.upgrade() {
                popup.paste_row(row.index());
            }
        });

        let key = gtk::EventControllerKey::new();
        key.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        key.connect_key_pressed(move |_, key, _, _| {
            if let Some(popup) = weak.upgrade() {
                match key {
                    gdk::Key::Escape => {
                        popup.window.set_visible(false);
                        return glib::Propagation::Stop;
                    }
                    gdk::Key::Down => {
                        popup.select_relative(1);
                        return glib::Propagation::Stop;
                    }
                    gdk::Key::Up => {
                        popup.select_relative(-1);
                        return glib::Propagation::Stop;
                    }
                    gdk::Key::Home => {
                        popup.select_index(0);
                        return glib::Propagation::Stop;
                    }
                    gdk::Key::End => {
                        let last = popup.rows.borrow().len().saturating_sub(1);
                        popup.select_index(last as i32);
                        return glib::Propagation::Stop;
                    }
                    gdk::Key::Return | gdk::Key::KP_Enter => {
                        if let Some(row) = popup.list.selected_row() {
                            popup.paste_row(row.index());
                            return glib::Propagation::Stop;
                        }
                    }
                    _ => {}
                }
            }
            glib::Propagation::Proceed
        });
        self.window.add_controller(key);
    }

    fn populate(self: &Rc<Self>) -> Result<()> {
        self.search.grab_focus();
        while let Some(child) = self.list.first_child() {
            self.list.remove(&child);
        }

        let query = self.search.text().to_string().to_lowercase();
        let entries = self
            .db
            .entries(100)?
            .into_iter()
            .filter(|entry| {
                query.trim().is_empty()
                    || entry
                        .content
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(query.trim())
            })
            .collect::<Vec<_>>();

        *self.rows.borrow_mut() = entries.clone();
        for entry in entries {
            self.list.append(&self.make_row(entry));
        }

        self.empty.set_visible(self.rows.borrow().is_empty());
        Ok(())
    }

    fn make_row(self: &Rc<Self>, entry: Entry) -> gtk::ListBoxRow {
        let row = gtk::ListBoxRow::new();
        let box_ = gtk::Box::new(gtk::Orientation::Horizontal, 4);
        box_.set_margin_start(4);
        box_.set_margin_end(2);
        box_.set_margin_top(4);
        box_.set_margin_bottom(4);

        let content_box = gtk::Box::new(gtk::Orientation::Vertical, 1);
        content_box.set_hexpand(true);
        content_box.set_valign(gtk::Align::Center);

        if entry.kind == "image" {
            if let Some(path) = &entry.image_path {
                if Path::new(path).exists() {
                    let picture = gtk::Picture::for_filename(path);
                    picture.set_content_fit(gtk::ContentFit::Contain);
                    picture.set_size_request(220, 80);
                    picture.set_halign(gtk::Align::Start);
                    content_box.append(&picture);
                }
            }
        } else {
            let preview = entry
                .content
                .as_deref()
                .unwrap_or("[empty]")
                .chars()
                .take(150)
                .collect::<String>()
                .replace(['\n', '\t'], " ");
            let label = gtk::Label::new(Some(&preview));
            label.set_halign(gtk::Align::Start);
            label.set_xalign(0.0);
            label.set_ellipsize(gtk::pango::EllipsizeMode::End);
            label.set_max_width_chars(45);
            if entry.pinned {
                label.add_css_class("heading");
            }
            content_box.append(&label);
        }

        let time = time_label(entry.created_at);
        let timestamp = gtk::Label::new(Some(&time));
        timestamp.set_halign(gtk::Align::Start);
        timestamp.add_css_class("caption");
        timestamp.add_css_class("dim-label");
        content_box.append(&timestamp);
        box_.append(&content_box);

        let pin = gtk::Button::from_icon_name("view-pin-symbolic");
        pin.add_css_class("flat");
        pin.add_css_class("circular");
        pin.set_tooltip_text(Some(if entry.pinned { "Unpin" } else { "Pin" }));
        let weak = Rc::downgrade(self);
        let id = entry.id;
        pin.connect_clicked(move |_| {
            if let Some(popup) = weak.upgrade() {
                if let Err(error) = popup.db.toggle_pin(id) {
                    eprintln!("[popup] pin failed: {error:#}");
                }
                let _ = popup.populate();
            }
        });
        box_.append(&pin);

        let delete = gtk::Button::from_icon_name("edit-delete-symbolic");
        delete.add_css_class("flat");
        delete.add_css_class("circular");
        delete.set_tooltip_text(Some("Delete"));
        let weak = Rc::downgrade(self);
        let id = entry.id;
        delete.connect_clicked(move |_| {
            if let Some(popup) = weak.upgrade() {
                if let Err(error) = popup.db.delete_entry(id) {
                    eprintln!("[popup] delete failed: {error:#}");
                }
                let _ = popup.populate();
            }
        });
        box_.append(&delete);

        row.set_child(Some(&box_));
        row
    }

    fn paste_row(&self, index: i32) {
        let Some(entry) = self.rows.borrow().get(index as usize).cloned() else {
            return;
        };

        let target = self.paste_target.borrow().clone();

        match entry.kind.as_str() {
            "text" => {
                if let Some(content) = entry.content {
                    let terminal = target.is_terminal();
                    let text = if terminal {
                        content.trim_end_matches('\n').to_string()
                    } else {
                        content
                    };
                    self.monitor.set_skip_hash(hash_bytes(text.as_bytes()));
                    self.monitor.set_text(&text);
                    self.window.set_visible(false);

                    let keyboard = self.keyboard.clone();
                    glib::timeout_add_local_once(Duration::from_millis(180), move || {
                        activate_window(target.window_id.as_deref());
                        keyboard.paste(terminal);
                    });
                }
            }
            "image" => {
                if let Some(path) = entry.image_path {
                    if self.monitor.set_image_from_path(&path) {
                        self.window.set_visible(false);
                        let keyboard = self.keyboard.clone();
                        glib::timeout_add_local_once(Duration::from_millis(180), move || {
                            activate_window(target.window_id.as_deref());
                            keyboard.paste(false);
                        });
                    }
                }
            }
            _ => {}
        }
    }

    fn select_relative(&self, delta: i32) {
        let len = self.rows.borrow().len() as i32;
        if len == 0 {
            return;
        }

        let current = self
            .list
            .selected_row()
            .map(|row| row.index())
            .unwrap_or(0);
        self.select_index((current + delta).clamp(0, len - 1));
    }

    fn select_index(&self, index: i32) {
        if let Some(row) = self.list.row_at_index(index) {
            self.list.select_row(Some(&row));
            row.grab_focus();
        }
    }

    fn move_to_screen_center(&self) {
        let Some(window_id) = find_popup_window_id() else {
            eprintln!("[popup] could not find X11 window id for move");
            return;
        };

        let (screen_w, screen_h) = display_size().unwrap_or((1600, 900));
        let x = ((screen_w - POPUP_WIDTH) / 2).max(12);
        let y = ((screen_h - POPUP_HEIGHT) / 2).max(12);

        let output = Command::new("xdotool")
            .args([
                "windowmove",
                &window_id,
                &x.to_string(),
                &y.to_string(),
            ])
            .output();

        match output {
            Ok(out) if out.status.success() => {
                eprintln!("[popup] centered xid={window_id} at {x},{y}");
            }
            Ok(out) => {
                eprintln!(
                    "[popup] xdotool windowmove failed: {}",
                    String::from_utf8_lossy(&out.stderr).trim()
                );
            }
            Err(error) => eprintln!("[popup] xdotool unavailable for move: {error}"),
        }
    }
}

fn time_label(created_at: f64) -> String {
    let system_time = UNIX_EPOCH + Duration::from_secs_f64(created_at);
    let datetime: DateTime<Local> = system_time.into();
    datetime.format("%H:%M").to_string()
}

fn current_paste_target() -> PasteTarget {
    let window_id = command_stdout("xdotool", &["getactivewindow"]);
    let wm_class = window_id
        .as_deref()
        .and_then(|id| command_stdout("xdotool", &["getwindowclassname", id]))
        .unwrap_or_default()
        .to_lowercase();

    PasteTarget {
        window_id,
        wm_class,
    }
}

fn is_terminal_class(class: &str) -> bool {
    let class = class.trim();
    class.contains("terminal")
        || matches!(
            class,
            "kgx" | "console" | "tilix" | "alacritty" | "kitty" | "xterm" | "konsole"
        )
}

fn activate_window(window_id: Option<&str>) {
    let Some(window_id) = window_id else {
        return;
    };

    let _ = Command::new("xdotool")
        .args(["windowactivate", window_id])
        .status();
}

fn find_popup_window_id() -> Option<String> {
    let pid = std::process::id().to_string();
    command_stdout("xdotool", &["search", "--pid", &pid, "--name", "Clipboard History"])
        .and_then(|ids| ids.lines().last().map(str::to_string))
}

fn command_stdout(command: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(command).args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

fn display_size() -> Option<(i32, i32)> {
    let text = command_stdout("xdotool", &["getdisplaygeometry"])?;
    let mut parts = text.split_whitespace();
    let width = parts.next()?.parse().ok()?;
    let height = parts.next()?.parse().ok()?;
    Some((width, height))
}
