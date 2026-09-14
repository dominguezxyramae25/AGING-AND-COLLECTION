"""Put an 'AR Collections' shortcut on the Desktop.

Run once:   python3 scripts/install_shortcut.py
Undo with:  python3 scripts/install_shortcut.py --remove

Detects the platform and writes the right kind of launcher: a double-clickable
.command on macOS, a .bat on Windows, and a .desktop entry on Linux.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
NAME = "AR Collections"


def desktop_dir() -> Path:
    """Find the Desktop, honouring localised and redirected locations."""
    home = Path.home()

    if platform.system() == "Windows":
        # OneDrive and localised Windows move the Desktop, so ask the shell.
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOPDIRECTORY = 0x10, SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
                return Path(buf.value)
        except Exception:                                          # noqa: BLE001
            pass
    else:
        # xdg-user-dir knows the localised name on Linux; harmless if missing.
        try:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                                 text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                found = Path(out.stdout.strip())
                if found.exists():
                    return found
        except (OSError, subprocess.SubprocessError):
            pass

    for candidate in (home / "Desktop", home / "Escritorio", home / "Bureau",
                      home / "OneDrive" / "Desktop"):
        if candidate.exists():
            return candidate
    return home


def shortcut_path() -> Path:
    system = platform.system()
    suffix = {"Darwin": ".command", "Windows": ".bat"}.get(system, ".desktop")
    return desktop_dir() / f"{NAME}{suffix}"


def _icon() -> str:
    for name in ("icon.png", "icon.svg"):
        candidate = PROJECT / "assets" / name
        if candidate.exists():
            return str(candidate)
    return "utilities-terminal"


def build() -> str:
    system = platform.system()
    if system == "Darwin":
        return (f'#!/bin/bash\n'
                f'# Double-click to start the AR Aging & Collections app.\n'
                f'exec "{PROJECT / "scripts" / "launch.sh"}"\n')
    if system == "Windows":
        return (f'@echo off\r\n'
                f'REM Double-click to start the AR Aging ^& Collections app.\r\n'
                f'call "{PROJECT / "scripts" / "launch.bat"}"\r\n')
    return (f"[Desktop Entry]\n"
            f"Type=Application\n"
            f"Name={NAME}\n"
            f"Comment=AR aging, DSO and collection effectiveness\n"
            f"Exec=\"{PROJECT / 'scripts' / 'launch.sh'}\"\n"
            f"Path={PROJECT}\n"
            f"Icon={_icon()}\n"
            f"Terminal=true\n"
            f"Categories=Office;Finance;\n")


def install() -> int:
    target = shortcut_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(build(), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write the shortcut: {exc}", file=sys.stderr)
        return 1

    if platform.system() != "Windows":
        target.chmod(0o755)

    if platform.system() == "Linux":
        # GNOME requires the launcher to be explicitly trusted before it will run.
        try:
            subprocess.run(["gio", "set", str(target), "metadata::trusted", "true"],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    print(f"Shortcut created: {target}")
    print()
    print("Double-click it to start the app. The first launch installs everything")
    print("it needs into .venv inside the project and takes about a minute;")
    print("after that it opens in a few seconds at http://localhost:8501")
    if platform.system() == "Darwin":
        print()
        print("macOS may warn that the file is from an unidentified developer the")
        print("first time. Right-click it, choose Open, then confirm.")
    return 0


def remove() -> int:
    target = shortcut_path()
    if target.exists():
        target.unlink()
        print(f"Removed {target}")
    else:
        print(f"Nothing to remove at {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="delete the shortcut")
    args = parser.parse_args()
    return remove() if args.remove else install()


if __name__ == "__main__":
    raise SystemExit(main())
