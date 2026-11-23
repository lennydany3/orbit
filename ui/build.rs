//! Puts Orbit's icon and version into orbit-ui.exe (what Explorer, the taskbar and the installer's shortcuts show). Windows only,
//! and a missing resource compiler is a warning, not a failure: the program is the same without the icon.

fn main() {
    println!("cargo:rerun-if-changed=assets/logo/orbit.ico");
    println!("cargo:rerun-if-changed=build.rs");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let mut resource = winresource::WindowsResource::new();
    resource.set_icon("assets/logo/orbit.ico");
    resource.set("ProductName", "Orbit");
    resource.set("FileDescription", "Orbit, your desktop assistant");
    resource.set("CompanyName", "Orbit");
    resource.set("LegalCopyright", "Orbit");
    resource.set("OriginalFilename", "orbit.exe");
    if let Err(why) = resource.compile() {
        println!("cargo:warning=no icon in the program ({why})");
    }
}
