use anyhow::{Context, Result};
use std::fs::OpenOptions;
use std::io;
use std::mem;
use std::os::fd::{AsRawFd, RawFd};
use std::process::Command;
use std::thread;
use std::time::Duration;

const EV_KEY: u16 = 0x01;
const EV_SYN: u16 = 0x00;
const SYN_REPORT: u16 = 0;
const KEY_LEFTCTRL: u16 = 29;
const KEY_LEFTSHIFT: u16 = 42;
const KEY_V: u16 = 47;

const UI_SET_EVBIT: libc::c_ulong = 1_074_025_828;
const UI_SET_KEYBIT: libc::c_ulong = 1_074_025_829;
const UI_DEV_CREATE: libc::c_ulong = 21_761;
const UI_DEV_DESTROY: libc::c_ulong = 21_762;
const BUS_USB: u16 = 0x03;

#[repr(C)]
#[derive(Clone, Copy)]
struct UInputUserDev {
    name: [libc::c_char; 80],
    id: InputId,
    ff_effects_max: u32,
    absmax: [i32; 64],
    absmin: [i32; 64],
    absfuzz: [i32; 64],
    absflat: [i32; 64],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

pub struct Keyboard {
    file: Option<std::fs::File>,
}

impl Keyboard {
    pub fn new() -> Self {
        match Self::open() {
            Ok(keyboard) => keyboard,
            Err(error) => {
                eprintln!("[paste] native uinput unavailable: {error:#}");
                Self { file: None }
            }
        }
    }

    pub fn paste(&self, terminal: bool) {
        if let Some(file) = &self.file {
            let keys = if terminal {
                &[KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V][..]
            } else {
                &[KEY_LEFTCTRL, KEY_V][..]
            };

            if let Err(error) = inject_chord(file.as_raw_fd(), keys) {
                eprintln!("[paste] uinput paste failed: {error:#}");
            } else {
                return;
            }
        }

        let key = if terminal { "ctrl+shift+v" } else { "ctrl+v" };
        if std::env::var_os("WAYLAND_DISPLAY").is_some() {
            let _ = Command::new("ydotool").args(["key", key]).status();
        } else {
            let _ = Command::new("xdotool").args(["key", key]).status();
        }
    }

    fn open() -> Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/uinput")
            .context("open /dev/uinput")?;
        let fd = file.as_raw_fd();

        ioctl(fd, UI_SET_EVBIT, EV_KEY as libc::c_ulong)?;
        for key in [KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V] {
            ioctl(fd, UI_SET_KEYBIT, key as libc::c_ulong)?;
        }

        let mut dev = UInputUserDev {
            name: [0; 80],
            id: InputId {
                bustype: BUS_USB,
                vendor: 0x1209,
                product: 0x0001,
                version: 1,
            },
            ff_effects_max: 0,
            absmax: [0; 64],
            absmin: [0; 64],
            absfuzz: [0; 64],
            absflat: [0; 64],
        };

        let name = b"clipboard-history-kbd\0";
        for (idx, byte) in name.iter().enumerate() {
            dev.name[idx] = *byte as libc::c_char;
        }

        let bytes = unsafe {
            std::slice::from_raw_parts(
                (&dev as *const UInputUserDev).cast::<u8>(),
                mem::size_of::<UInputUserDev>(),
            )
        };

        nix_write(fd, bytes)?;
        ioctl(fd, UI_DEV_CREATE, 0)?;
        thread::sleep(Duration::from_millis(250));

        Ok(Self { file: Some(file) })
    }
}

impl Drop for Keyboard {
    fn drop(&mut self) {
        if let Some(file) = &self.file {
            let _ = ioctl(file.as_raw_fd(), UI_DEV_DESTROY, 0);
        }
    }
}

fn inject_chord(fd: RawFd, keys: &[u16]) -> Result<()> {
    for key in keys {
        emit(fd, EV_KEY, *key, 1)?;
    }
    sync(fd)?;
    thread::sleep(Duration::from_millis(20));

    for key in keys.iter().rev() {
        emit(fd, EV_KEY, *key, 0)?;
    }
    sync(fd)?;
    Ok(())
}

fn sync(fd: RawFd) -> Result<()> {
    emit(fd, EV_SYN, SYN_REPORT, 0)
}

fn emit(fd: RawFd, kind: u16, code: u16, value: i32) -> Result<()> {
    let event = libc::input_event {
        time: libc::timeval {
            tv_sec: 0,
            tv_usec: 0,
        },
        type_: kind,
        code,
        value,
    };
    let bytes = unsafe {
        std::slice::from_raw_parts(
            (&event as *const libc::input_event).cast::<u8>(),
            mem::size_of::<libc::input_event>(),
        )
    };
    nix_write(fd, bytes)
}

fn ioctl(fd: RawFd, request: libc::c_ulong, value: libc::c_ulong) -> Result<()> {
    let rc = unsafe { libc::ioctl(fd, request, value) };
    if rc < 0 {
        Err(io::Error::last_os_error()).context("ioctl")
    } else {
        Ok(())
    }
}

fn nix_write(fd: RawFd, bytes: &[u8]) -> Result<()> {
    let rc = unsafe { libc::write(fd, bytes.as_ptr().cast(), bytes.len()) };
    if rc < 0 {
        Err(io::Error::last_os_error()).context("write")
    } else {
        Ok(())
    }
}
