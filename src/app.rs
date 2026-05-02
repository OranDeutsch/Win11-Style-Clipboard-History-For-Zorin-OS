use crate::db::Database;
use crate::monitor::ClipboardMonitor;
use crate::paste::Keyboard;
use crate::ui::ClipboardPopup;
use anyhow::{Context, Result};
use gtk::prelude::*;
use std::cell::RefCell;
use std::fs;
use std::io::Read;
use std::os::unix::net::UnixListener;
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

thread_local! {
    static APP_STATE: RefCell<Option<Rc<ClipboardHistoryApp>>> = const { RefCell::new(None) };
}

enum Command {
    Show,
    Quit,
}

pub struct ClipboardHistoryApp {
    popup: RefCell<Option<Rc<ClipboardPopup>>>,
    _hold: gio::ApplicationHoldGuard,
}

impl ClipboardHistoryApp {
    pub fn start(app: &gtk::Application) -> Result<Rc<Self>> {
        fs::create_dir_all(crate::data_dir())?;

        let db = Rc::new(Database::open()?);
        let monitor = ClipboardMonitor::start(db.clone())
            .context("no GTK display found for clipboard monitoring")?;
        let keyboard = Rc::new(Keyboard::new());

        let state = Rc::new(Self {
            popup: RefCell::new(None),
            _hold: app.hold(),
        });

        let popup = ClipboardPopup::new(app, db, monitor, keyboard);
        *state.popup.borrow_mut() = Some(popup);

        let (sender, receiver) = mpsc::channel();
        start_socket_thread(sender)?;

        let app_weak = app.downgrade();
        let state_weak = Rc::downgrade(&state);
        glib::timeout_add_local(Duration::from_millis(40), move || {
            let Some(app) = app_weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let Some(state) = state_weak.upgrade() else {
                return glib::ControlFlow::Break;
            };

            while let Ok(command) = receiver.try_recv() {
                match command {
                    Command::Show => {
                        eprintln!("[clipboard-history] SHOW command received");
                        if let Some(popup) = state.popup.borrow().as_ref() {
                            popup.toggle();
                        }
                    }
                    Command::Quit => {
                        eprintln!("[clipboard-history] QUIT command received");
                        app.quit();
                    }
                }
            }

            glib::ControlFlow::Continue
        });

        eprintln!("[clipboard-history] Rust daemon started");
        APP_STATE.with(|slot| {
            *slot.borrow_mut() = Some(state.clone());
        });
        Ok(state)
    }
}

fn start_socket_thread(sender: mpsc::Sender<Command>) -> Result<()> {
    let path = crate::socket_path();
    let _ = fs::remove_file(&path);
    let listener = UnixListener::bind(&path)?;

    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else {
                continue;
            };

            let mut buf = [0_u8; 32];
            let Ok(n) = stream.read(&mut buf) else {
                continue;
            };

            let command = std::str::from_utf8(&buf[..n]).unwrap_or("").trim();
            match command {
                "SHOW" => {
                    let _ = sender.send(Command::Show);
                }
                "QUIT" => {
                    let _ = sender.send(Command::Quit);
                    break;
                }
                _ => eprintln!("[clipboard-history] unknown socket command: {command:?}"),
            }
        }
    });

    Ok(())
}
