use crate::db::Database;
use crate::monitor::ClipboardMonitor;
use crate::paste::Keyboard;
use anyhow::{Context, Result};
use gtk::prelude::*;
use std::cell::RefCell;
use std::fs;
use std::io::{Read, Write};
use std::os::unix::net::UnixListener;
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

thread_local! {
    static APP_STATE: RefCell<Option<Rc<ClipboardHistoryApp>>> = const { RefCell::new(None) };
}

enum Command {
    List { reply: mpsc::Sender<String> },
    Paste {
        id: i64,
        terminal: bool,
        reply: mpsc::Sender<String>,
    },
    Pin { id: i64, reply: mpsc::Sender<String> },
    Delete { id: i64, reply: mpsc::Sender<String> },
    Clear { reply: mpsc::Sender<String> },
    Quit { reply: mpsc::Sender<String> },
}

pub struct ClipboardHistoryApp {
    db: Rc<Database>,
    monitor: Rc<ClipboardMonitor>,
    keyboard: Rc<Keyboard>,
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
            db,
            monitor,
            keyboard,
            _hold: app.hold(),
        });

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
                    Command::List { reply } => {
                        let response = state.list_entries();
                        let _ = reply.send(response);
                    }
                    Command::Paste {
                        id,
                        terminal,
                        reply,
                    } => {
                        let response = state.paste_entry(id, terminal);
                        let _ = reply.send(response);
                    }
                    Command::Pin { id, reply } => {
                        let response = state
                            .db
                            .toggle_pin(id)
                            .map(|_| ok_json())
                            .unwrap_or_else(error_json);
                        let _ = reply.send(response);
                    }
                    Command::Delete { id, reply } => {
                        let response = state
                            .db
                            .delete_entry(id)
                            .map(|_| ok_json())
                            .unwrap_or_else(error_json);
                        let _ = reply.send(response);
                    }
                    Command::Clear { reply } => {
                        let response = state
                            .db
                            .clear_unpinned()
                            .map(|_| ok_json())
                            .unwrap_or_else(error_json);
                        let _ = reply.send(response);
                    }
                    Command::Quit { reply } => {
                        eprintln!("[clipboard-history] QUIT command received");
                        let _ = reply.send(ok_json());
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

    fn list_entries(&self) -> String {
        self.db
            .entries(100)
            .and_then(|entries| serde_json::to_string(&entries).map_err(Into::into))
            .unwrap_or_else(error_json)
    }

    fn paste_entry(&self, id: i64, terminal: bool) -> String {
        match self.db.entry(id) {
            Ok(Some(entry)) if entry.kind == "text" => {
                if let Some(content) = entry.content {
                    let text = if terminal {
                        content.trim_end_matches('\n').to_string()
                    } else {
                        content
                    };
                    self.monitor.set_text(&text);
                    let keyboard = self.keyboard.clone();
                    glib::timeout_add_local_once(Duration::from_millis(120), move || {
                        keyboard.paste(terminal);
                    });
                    ok_json()
                } else {
                    error_json(anyhow::anyhow!("text entry has no content"))
                }
            }
            Ok(Some(entry)) if entry.kind == "image" => {
                if let Some(path) = entry.image_path {
                    if self.monitor.set_image_from_path(&path) {
                        let keyboard = self.keyboard.clone();
                        glib::timeout_add_local_once(Duration::from_millis(120), move || {
                            keyboard.paste(false);
                        });
                        ok_json()
                    } else {
                        error_json(anyhow::anyhow!("failed to set image clipboard"))
                    }
                } else {
                    error_json(anyhow::anyhow!("image entry has no image path"))
                }
            }
            Ok(Some(entry)) => error_json(anyhow::anyhow!("unsupported entry type {}", entry.kind)),
            Ok(None) => error_json(anyhow::anyhow!("entry {id} not found")),
            Err(error) => error_json(error),
        }
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

            let mut buf = [0_u8; 256];
            let Ok(n) = stream.read(&mut buf) else {
                continue;
            };

            let raw = std::str::from_utf8(&buf[..n]).unwrap_or("").trim();
            let (reply_tx, reply_rx) = mpsc::channel();
            let command = parse_command(raw, reply_tx);
            let should_quit = matches!(command, Some(Command::Quit { .. }));

            match command {
                Some(command) => {
                    if sender.send(command).is_err() {
                        let _ = stream.write_all(error_json(anyhow::anyhow!("daemon stopped")).as_bytes());
                        continue;
                    }
                    match reply_rx.recv_timeout(Duration::from_secs(2)) {
                        Ok(response) => {
                            let _ = stream.write_all(response.as_bytes());
                        }
                        Err(error) => {
                            let _ = stream.write_all(error_json(error).as_bytes());
                        }
                    }
                    if should_quit {
                        break;
                    }
                }
                None => {
                    let _ = stream.write_all(error_json(anyhow::anyhow!("unknown command: {raw}")).as_bytes());
                }
            }
        }
    });

    Ok(())
}

fn parse_command(raw: &str, reply: mpsc::Sender<String>) -> Option<Command> {
    let mut parts = raw.split_whitespace();
    match parts.next()? {
        "LIST" => Some(Command::List { reply }),
        "PASTE" => {
            let id = parts.next()?.parse().ok()?;
            let terminal = parts.next() == Some("terminal");
            Some(Command::Paste {
                id,
                terminal,
                reply,
            })
        }
        "PIN" => Some(Command::Pin {
            id: parts.next()?.parse().ok()?,
            reply,
        }),
        "DELETE" => Some(Command::Delete {
            id: parts.next()?.parse().ok()?,
            reply,
        }),
        "CLEAR" => Some(Command::Clear { reply }),
        "QUIT" => Some(Command::Quit { reply }),
        _ => None,
    }
}

fn ok_json() -> String {
    r#"{"ok":true}"#.to_string()
}

fn error_json(error: impl std::fmt::Display) -> String {
    serde_json::json!({
        "ok": false,
        "error": error.to_string(),
    })
    .to_string()
}
