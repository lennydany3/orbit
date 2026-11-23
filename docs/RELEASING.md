# Releasing Orbit

Every push to `main` is a release. Nothing to run by hand.

```
push / merge to main
  -> .github/workflows/release.yml
       test      Python tests + the window's own Rust tests
       release   1. version      <VERSION file>.<run number>, e.g. 1.0.17, stamped into ui/Cargo.toml and pyproject.toml
                 2. engine       PyInstaller: packaging/orbit-engine.spec -> build/dist/orbit-engine/ (Python and every library inside)
                 3. smoke test   the built engine answers "what time is it"
                 4. window       cargo build --release in ui/ -> orbit-ui.exe
                 5. installer    Inno Setup: installer/orbit.iss -> Orbit-Setup-<version>.exe
                 6. publish      GitHub release v<version> with the installer and its SHA-256, notes from the merged pull requests
```

Pull requests run `ci.yml` (the same tests) before they can be merged.

## What gets installed

| Where | What |
|---|---|
| `%LOCALAPPDATA%\Programs\Orbit\Orbit.exe` | the window, the tray and the orb |
| `%LOCALAPPDATA%\Programs\Orbit\engine\` | the engine: Orbit's voice and brain, a program of its own (no Python needed on the PC) |
| `%APPDATA%\Orbit\` | **yours**: `settings.json`, reminders, notes, music, `.env` (keys), `models\`, `logs\`. Updates never touch it |

The installer is per-user, so it needs no administrator rights, and neither does an update.

## How an installed Orbit updates itself

`ui/src/updater.rs`: 20 seconds after start and then every 6 hours, Orbit asks GitHub for the latest release of this repository,
and if its version is newer it downloads the installer to `%LOCALAPPDATA%\Orbit\updates\`, checks the size and SHA-256 GitHub lists, and
shows **Restart and update** in the sidebar and the tray menu. The click runs the installer silently (`/SILENT`), which closes
Orbit, replaces the program and starts it again. Drafts and pre-releases are never offered.

Switch it off for one run with `ORBIT_NO_UPDATE=1`; point a fork at its own releases with `ORBIT_UPDATE_REPO=owner/name`.

## Versions

`VERSION` holds the first two numbers (`1.0`); the workflow adds the run number. Edit `VERSION` to start a `1.1` or `2.0` line. The
last number only ever goes up, which is all the updater needs.

## Doing it by hand

```
pip install -e ".[voice,ipc]" pyinstaller
pyinstaller packaging/orbit-engine.spec --noconfirm --distpath build/dist --workpath build/work
cd ui && cargo build --release && cd ..
iscc /DAppVersion=1.0.0 installer\orbit.iss          # Inno Setup 6, installer\Output\Orbit-Setup-1.0.0.exe
```

The installer's pictures are drawn by `python installer/make_wizard_images.py`; the icon by `python ui/assets/logo/make_icons.py`.

## Things to know

- **Unsigned.** The installer is not code-signed, so Windows SmartScreen shows "unknown publisher" the first time ("More info", "Run
  anyway"). A code-signing certificate removes that; add it to the workflow with `signtool` before the checksum step.
- **Keys are never shipped.** Groq, Sarvam and Spotify keys belong to each person; the installer can save the ones they paste to
  `%APPDATA%\Orbit\.env`.
- **Models.** The small ones (voice-activity detector, the "hey orbit" wake word) are in `packaging/models/` and ship inside the installer;
  the speech and voice models download on first run (`orbit/voice/provision.py`).
