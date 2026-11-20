//! Starting Orbit when you sign in to Windows: one value in your own `Run` key (no administrator rights, nothing outside your
//! account). Never done on its own: only `orbit-ui --autostart on` writes it, and `--autostart off` takes it out again.
//!
//! The command starts the window through a hidden PowerShell, because `orbit-ui` is a console program and would otherwise
//! leave a console window open all day. The console it inherits is the hidden one, so Orbit's own messages go nowhere visible.

use std::path::Path;
use std::process::Command;

const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
/// The value name for the real thing.
pub const NAME: &str = "Orbit";

/// A PowerShell single-quoted string: the only character to escape is the quote itself.
fn quoted(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

/// What Windows runs at sign-in.
pub fn command_line(exe: &Path, args: &[&str]) -> String {
    let mut call = format!("& {}", quoted(&exe.to_string_lossy()));
    for arg in args {
        call.push(' ');
        call.push_str(&quoted(arg));
    }
    format!("powershell.exe -NoProfile -WindowStyle Hidden -Command \"{call}\"")
}

fn reg(args: &[&str]) -> Result<String, String> {
    let out = Command::new("reg").args(args).output().map_err(|e| format!("could not run reg.exe: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_owned())
    }
}

/// Starts `exe args` at every sign-in.
pub fn enable(name: &str, exe: &Path, args: &[&str]) -> Result<(), String> {
    reg(&["add", RUN_KEY, "/v", name, "/t", "REG_SZ", "/d", &command_line(exe, args), "/f"]).map(|_| ())
}

/// Stops starting it. Fine if it was never set.
pub fn disable(name: &str) -> Result<(), String> {
    match reg(&["delete", RUN_KEY, "/v", name, "/f"]) {
        Ok(_) => Ok(()),
        Err(_) if current(name).is_none() => Ok(()),
        Err(e) => Err(e),
    }
}

/// The command set for `name`, if there is one.
pub fn current(name: &str) -> Option<String> {
    let out = reg(&["query", RUN_KEY, "/v", name]).ok()?;
    let line = out.lines().find(|l| l.trim_start().starts_with(name))?;
    line.split_once("REG_SZ").map(|(_, data)| data.trim().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_command_hides_the_console_and_passes_each_argument_quoted() {
        let c = command_line(Path::new(r"C:\Orbit\orbit-ui.exe"), &["--spawn"]);
        assert_eq!(c, r#"powershell.exe -NoProfile -WindowStyle Hidden -Command "& 'C:\Orbit\orbit-ui.exe' '--spawn'""#);
    }

    #[test]
    fn a_path_with_spaces_or_a_quote_cannot_break_the_command() {
        let c = command_line(Path::new(r"C:\Users\O'Brien\My Apps\orbit-ui.exe"), &["--url", "ws://127.0.0.1:1/?a=b&c=d"]);
        assert!(c.contains(r"'C:\Users\O''Brien\My Apps\orbit-ui.exe'"), "{c}");
        assert!(c.contains("'ws://127.0.0.1:1/?a=b&c=d'"), "{c}");
        assert_eq!(c.matches('"').count(), 2, "one pair of double quotes around the whole call");
    }
}
