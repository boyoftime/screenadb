"""Builds dist/screenadb-<version>-windows.zip: a screenadb folder with screenadb.exe and everything it needs
(Python, Pillow, the app's images) in _internal beside it. Unzip anywhere and run screenadb.exe; no Python needed.

    py -m pip install pyinstaller pillow
    py build_exe.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"

subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onedir",  # a folder, not one file: no unpacking on every start, so it opens fast
    "--windowed",  # no console window behind the app
    "--name", "screenadb",
    # Pillow would pull in numpy (about 27 MB) for features screenadb never uses.
    "--exclude-module", "numpy",
    "--icon", str(HERE / "screenadb.ico"),
    "--add-data", f"{HERE / 'screenadb.ico'};.",
    "--add-data", f"{HERE / 'screenadb.png'};.",
    "--add-data", f"{HERE / 'someless_tricks.png'};.",
    "--distpath", str(DIST),
    "--workpath", str(HERE / "build"),
    "--specpath", str(HERE / "build"),
    str(HERE / "screenadb.pyw"),
], check=True)

version = re.search(r'VERSION = "([^"]+)"', (HERE / "screenadb.pyw").read_text(encoding="utf-8")).group(1)
folder = DIST / "screenadb"
# Beside the exe, for anyone who opens the folder.
(folder / "README.txt").write_text(
    f"screenadb {version}: phone screenshots over adb, by USB or Wi-Fi.\n"
    "\n"
    "Run screenadb.exe. Nothing else to install: if adb isn't on this PC, screenadb offers to download it.\n"
    "Keep this folder together: screenadb.exe needs the _internal folder beside it.\n"
    "\n"
    "More: https://github.com/boyoftime/screenadb\n",
    encoding="utf-8",
)
archive = shutil.make_archive(str(DIST / f"screenadb-{version}-windows"), "zip", DIST, "screenadb")
print(f"Built {archive}")
