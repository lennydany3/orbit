//! A stand-in for the Inno Setup installer, for trying Orbit's installer window without installing anything:
//! it writes the log lines Inno Setup writes (one "Dest filename:" per file) and records the options it was given.
//!
//!     cargo build --example fake_inno
//!     python installer/pack.py --setup target/debug/orbit-setup.exe --inner target/debug/examples/fake_inno.exe --files 300 --out x.exe
//!
//! FAKE_INNO_FILES (300) files, FAKE_INNO_MS (12) ms each, exit code FAKE_INNO_EXIT (0). The options go to `<log>.args`.

use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let get = |name: &str, default: u64| std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default);
    let log = args.iter().find_map(|a| a.strip_prefix("/LOG=")).map(std::path::PathBuf::from);
    let dir = args.iter().find_map(|a| a.strip_prefix("/DIR=")).unwrap_or(r"C:\Orbit");
    if let Some(log) = &log {
        std::fs::write(log.with_extension("args"), args.join("\n")).unwrap();
        if let Ok(record) = std::env::var("FAKE_INNO_RECORD") {
            std::fs::write(record, args.join("\n")).unwrap();    // the work folder is removed after a good install
        }
        let mut f = std::fs::File::create(log).unwrap();
        writeln!(f, "Log opened.\nStarting the installation process.").unwrap();
        for i in 0..get("FAKE_INNO_FILES", 300) {
            writeln!(f, "-- File entry --\nDest filename: {dir}/engine/file{i}.dll").unwrap();
            f.flush().unwrap();
            std::thread::sleep(std::time::Duration::from_millis(get("FAKE_INNO_MS", 12)));
        }
        writeln!(f, "Installation process succeeded.").unwrap();
    }
    std::process::exit(get("FAKE_INNO_EXIT", 0) as i32);
}
