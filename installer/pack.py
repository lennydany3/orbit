"""Makes the installer people download: Orbit's own installer window with the real (Inno Setup) installer inside it.

    python installer/pack.py --version 1.0.7          (after `cargo build --release` in ui/ and `iscc installer/orbit.iss`)
    python installer/pack.py --check installer/Output/Orbit-Setup-1.0.7.exe

writes installer/Output/Orbit-Setup-<version>.exe as

    [ui/target/release/orbit-setup.exe][installer/Output/Orbit-Core-Setup-<version>.exe][length: u64][files: u64][b"ORBITPK1"]

`files` is how many files the inner installer writes (Orbit.exe and everything in build/dist/orbit-engine), so the window can
show real progress from the installer's log. The layout must match ui/src/setup/install.rs.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAGIC = b"ORBITPK1"
FOOTER = struct.Struct("<QQ8s")


def count_files(engine: Path) -> int:
    """Orbit.exe plus every file of the engine: what the inner installer copies."""
    return 1 + sum(1 for f in engine.rglob("*") if f.is_file())


def pack(setup: Path, inner: Path, files: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as dst:
        with setup.open("rb") as src:
            shutil.copyfileobj(src, dst)
        with inner.open("rb") as src:
            shutil.copyfileobj(src, dst, length=1 << 20)
        dst.write(FOOTER.pack(inner.stat().st_size, files, MAGIC))


def read_footer(packed: Path) -> tuple[int, int] | None:
    """(payload length, files) or None when nothing is appended."""
    with packed.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size < FOOTER.size:
            return None
        f.seek(size - FOOTER.size)
        length, files, magic = FOOTER.unpack(f.read(FOOTER.size))
    if magic != MAGIC or not 0 < length <= size - FOOTER.size:
        return None
    return length, files


def check(packed: Path, inner: Path | None = None) -> int:
    footer = read_footer(packed)
    if footer is None:
        print(f"{packed}: no Orbit installer inside", file=sys.stderr)
        return 1
    length, files = footer
    if inner is not None:
        with packed.open("rb") as f:
            f.seek(packed.stat().st_size - FOOTER.size - length)
            head = f.read(2)
        if length != inner.stat().st_size or head != b"MZ":
            print(f"{packed}: the installer inside does not match {inner}", file=sys.stderr)
            return 1
    print(f"{packed.name}: {packed.stat().st_size / 1e6:.1f} MB, carries a {length / 1e6:.1f} MB installer that writes {files} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", help="the release version (names the files)")
    p.add_argument("--setup", type=Path, default=ROOT / "ui" / "target" / "release" / "orbit-setup.exe")
    p.add_argument("--inner", type=Path, help="default: installer/Output/Orbit-Core-Setup-<version>.exe")
    p.add_argument("--engine", type=Path, default=ROOT / "build" / "dist" / "orbit-engine")
    p.add_argument("--out", type=Path, help="default: installer/Output/Orbit-Setup-<version>.exe")
    p.add_argument("--files", type=int, help="how many files the inner installer writes (default: counted from --engine)")
    p.add_argument("--check", type=Path, metavar="EXE", help="only check a packed installer")
    args = p.parse_args(argv)
    if args.check:
        return check(args.check, args.inner)
    if not args.version and not (args.inner and args.out):
        p.error("--version is needed (or both --inner and --out)")
    inner = args.inner or ROOT / "installer" / "Output" / f"Orbit-Core-Setup-{args.version}.exe"
    out = args.out or ROOT / "installer" / "Output" / f"Orbit-Setup-{args.version}.exe"
    for need in (args.setup, inner):
        if not need.is_file():
            print(f"missing {need}", file=sys.stderr)
            return 1
    files = args.files if args.files is not None else count_files(args.engine) if args.engine.is_dir() else 0
    pack(args.setup, inner, files, out)
    return check(out, inner)


if __name__ == "__main__":
    raise SystemExit(main())
