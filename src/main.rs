mod app;
mod db;
mod monitor;
mod paste;
mod ui;

use anyhow::{Context, Result};
use gtk::prelude::*;
use std::env;
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

const APP_ID: &str = "tech.missionzero.clipboard-history";

fn data_dir() -> PathBuf {
    dirs::home_dir()
        .expect("home directory")
        .join(".local/share/clipboard-history")
}

fn socket_path() -> PathBuf {
    data_dir().join("control.sock")
}

fn send_command(command: &str) -> Result<()> {
    let mut stream = UnixStream::connect(socket_path())
        .context("clipboard-history daemon is not running")?;
    stream.write_all(command.as_bytes())?;
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if let Some(arg) = args.get(1) {
        match arg.as_str() {
            "--show" => return send_command("SHOW"),
            "--quit" => return send_command("QUIT"),
            _ => {}
        }
    }

    let application = gtk::Application::builder()
        .application_id(APP_ID)
        .build();

    application.connect_activate(|gtk_app| {
        if let Err(error) = app::ClipboardHistoryApp::start(gtk_app) {
            eprintln!("[clipboard-history] startup failed: {error:#}");
            gtk_app.quit();
        }
    });

    application.run();
    Ok(())
}
