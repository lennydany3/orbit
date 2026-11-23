# PyInstaller recipe for Orbit's engine (the Python half). Build from the project folder:
#
#     pip install -e ".[voice,ipc]" pyinstaller
#     pyinstaller packaging/orbit-engine.spec --noconfirm --distpath build/dist --workpath build/work
#
# The result is build/dist/orbit-engine/ (orbit-engine.exe and its _internal folder), which the installer puts next to orbit.exe.
# It is a console program on purpose: the window starts it with no console window and talks to it through a pipe.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent

datas, binaries, hiddenimports = [], [], []
for package in ("piper", "faster_whisper", "ctranslate2", "openwakeword", "onnxruntime", "sounddevice", "playwright", "pyttsx3", "pycaw", "comtypes"):
    try:
        d, b, h = collect_all(package)
    except Exception as e:                                   # a package that is not installed is simply not shipped
        print(f"orbit-engine.spec: skipping {package}: {e}")
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("orbit")
hiddenimports += collect_submodules("winrt")
hiddenimports += collect_submodules("pynput")
hiddenimports += ["pyttsx3.drivers", "pyttsx3.drivers.sapi5", "websockets.legacy", "websockets.sync.server"]

datas += [
    (str(root / "src" / "orbit" / "voice" / "alarms"), "orbit/voice/alarms"),      # the reminder and timer sounds
    (str(root / "packaging" / "models"), "models"),                                  # the small models; the big ones download on first run
]

a = Analysis(
    [str(root / "packaging" / "engine_entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow", "tflite_runtime", "scipy.tests", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="orbit-engine", console=True, upx=False, icon=str(root / "ui" / "assets" / "logo" / "orbit.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="orbit-engine")
