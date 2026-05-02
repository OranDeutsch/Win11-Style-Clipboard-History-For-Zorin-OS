mod app;
mod db;
mod monitor;
mod paste;

use anyhow::{Context, Result};
use gtk::prelude::*;
use std::env;
use std::io::{Read, Write};
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

fn send_command(command: &str) -> Result<String> {
    let mut stream = UnixStream::connect(socket_path())
        .context("clipboard-history daemon is not running")?;
    stream.write_all(command.as_bytes())?;
    stream.shutdown(std::net::Shutdown::Write).ok();

    let mut response = String::new();
    stream.read_to_string(&mut response)?;
    Ok(response)
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if let Some(arg) = args.get(1) {
        match arg.as_str() {
            "--show" => return Ok(()),
            "--list" => {
                print!("{}", send_command("LIST")?);
                return Ok(());
            }
            "--paste" => {
                let id = args.get(2).context("--paste requires an entry id")?;
                let terminal = args.get(3).is_some_and(|value| value == "--terminal");
                let response = send_command(&format!(
                    "PASTE {id} {}",
                    if terminal { "terminal" } else { "normal" }
                ))?;
                print!("{response}");
                return Ok(());
            }
            "--pin" => {
                let id = args.get(2).context("--pin requires an entry id")?;
                print!("{}", send_command(&format!("PIN {id}"))?);
                return Ok(());
            }
            "--delete" => {
                let id = args.get(2).context("--delete requires an entry id")?;
                print!("{}", send_command(&format!("DELETE {id}"))?);
                return Ok(());
            }
            "--clear" => {
                print!("{}", send_command("CLEAR")?);
                return Ok(());
            }
            "--quit" => {
                print!("{}", send_command("QUIT")?);
                return Ok(());
            }
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
