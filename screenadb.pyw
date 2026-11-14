"""screenadb: grab an Android phone's screen over adb, straight onto the clipboard or into a file.

Double-click screenadb.lnk (or run `pyw screenadb.pyw`). Keys: C copies, S saves, O opens the shots folder,
P keeps the window on top, Esc leaves Settings. Saved shots go in the shots folder beside this file.

For speed, while the pointer is over the window, fresh shots keep coming in the background, so a click (or C / S)
is near instant: it uses a shot the phone took within the last second. Away from the window, a click asks the
phone directly, which takes about half a second.

New PC without adb? screenadb offers to download Google's official platform-tools into its own folder.

Wireless: once a phone has been on Wi-Fi with screenadb (switched from USB, or paired with a code in Settings), it
reconnects by itself whenever no phone is ready, as long as the phone and PC share the Wi-Fi.
Made by Someless Tricks.
"""
import ctypes
import io
import json
import math
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
import zipfile
import zlib
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    tk.Tk().withdraw()
    messagebox.showerror("screenadb", "screenadb needs Pillow.\n\nRun:  py -m pip install pillow")
    raise SystemExit(1)

VERSION = "1.2"
HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SETTINGS = HERE / "settings.json"
ICON_ICO = HERE / "screenadb.ico"
ICON_PNG = HERE / "screenadb.png"
MAKER_LOGO = HERE / "someless_tricks.png"
# adb that screenadb downloads for itself lives here, beside the app.
LOCAL_TOOLS = HERE / "platform-tools"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"

POLL_MS = 2000
# With no phone ready, Wi-Fi phones are looked for this often (a connect attempt can take a few seconds).
RECONNECT_SECONDS = 6
# The port a USB phone is switched to for Wi-Fi.
WIFI_PORT = 5555
# Wi-Fi addresses kept to reconnect, newest first.
MAX_REMEMBERED = 6
# How often finished background work is picked up by the window; short, so a click's result shows at once.
PUMP_MS = 12
POINTER_MS = 120
# A background shot started less than this long ago is used for a click straight away. The phone needs about
# half a second per shot, so with shots coming back to back the newest is always within this.
FRESH_SECONDS = 1.0
# Under pythonw there's no console, and without this every adb call would flash one up.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ADB_PORT = int(os.environ.get("ANDROID_ADB_SERVER_PORT", "5037"))
# screencap's raw pixel formats Pillow can read directly, by Android's format number.
RAW_MODES = {1: "RGBA", 2: "RGBX", 5: "BGRA"}

# The AccuPilot Pro palettes. Coral is the same in both; C is the one in use, swapped in place on a theme change.
CORAL = "#FF444F"
CORAL_HOVER = "#FF5C66"
CORAL_PRESSED = "#C41C28"
THEMES = {
    "dark": {
        "bg": "#11141B", "surface": "#1B1F29", "surface_hover": "#232836", "border": "#2A2F3B",
        "text": "#FFFFFF", "muted": "#9195A1", "green": "#34D399", "amber": "#F5B041", "red": "#FF6B73",
        "coral_disabled": "#5A2A31", "coral_disabled_text": "#C9A9AD", "phone": "#5C616E",
    },
    "light": {
        "bg": "#F6F7F9", "surface": "#FFFFFF", "surface_hover": "#EEF0F3", "border": "#DADDE3",
        "text": "#181C25", "muted": "#5C616E", "green": "#0E9F6E", "amber": "#B86E00", "red": "#C41C28",
        "coral_disabled": "#F5C6CA", "coral_disabled_text": "#FFFFFF", "phone": "#B1B4BC",
    },
}
C = dict(THEMES["dark"])


def load_settings():
    """What the user chose last time: theme, keep on top, and where the window was."""
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    position = saved.get("position")
    valid_position = isinstance(position, list) and len(position) == 2 and all(isinstance(v, int) for v in position)
    wireless = saved.get("wireless")
    return {
        "theme": saved.get("theme") if saved.get("theme") in THEMES else "dark",
        "pinned": bool(saved.get("pinned")),
        "position": position if valid_position else None,
        "wireless": [a for a in wireless if isinstance(a, str)][:MAX_REMEMBERED] if isinstance(wireless, list) else [],
    }


def save_settings(settings):
    try:
        SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- adb


def find_adb():
    """screenadb's own adb if it downloaded one, else adb on PATH or in the usual SDK spots, or None."""
    if (LOCAL_TOOLS / "adb.exe").is_file():
        return str(LOCAL_TOOLS / "adb.exe")
    found = shutil.which("adb")
    if found:
        return found
    home = Path.home()
    candidates = [Path(os.environ[name]) / "platform-tools" / "adb.exe"
                  for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(name)]
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    candidates += [
        home / "platform-tools" / "adb.exe",
        home / "platform-tools" / "platform-tools" / "adb.exe",
        Path("C:/platform-tools/adb.exe"),
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def download_platform_tools(progress):
    """Fetches Google's official platform-tools and unpacks it into LOCAL_TOOLS. progress gets 0..1 as it downloads.

    The new copy is unpacked beside the old one first, so a failed download never leaves adb half-replaced.
    """
    staging = HERE / "platform-tools.new"
    shutil.rmtree(staging, ignore_errors=True)
    with tempfile.TemporaryDirectory() as folder:
        archive = Path(folder) / "platform-tools.zip"
        with urllib.request.urlopen(PLATFORM_TOOLS_URL, timeout=30) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                received += len(chunk)
                if total:
                    progress(received / total)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
    if not (staging / "platform-tools" / "adb.exe").is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("The download didn't contain adb.")
    # The old copy's server has to stop before its files can be replaced.
    if (LOCAL_TOOLS / "adb.exe").is_file():
        try:
            run_adb(str(LOCAL_TOOLS / "adb.exe"), "kill-server", timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(LOCAL_TOOLS, ignore_errors=True)
    (staging / "platform-tools").rename(LOCAL_TOOLS)
    shutil.rmtree(staging, ignore_errors=True)
    return str(LOCAL_TOOLS / "adb.exe")


def run_adb(adb, *args, timeout=15):
    return subprocess.run([adb, *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def adb_version(adb):
    """Platform-tools version, e.g. "37.0.0", or "" if adb won't say."""
    try:
        out = run_adb(adb, "version", timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.startswith("Version "):
            return line.split()[1].split("-")[0]
    return ""


def list_devices(adb):
    """Every phone adb sees, as dicts of serial, state (device / unauthorized / offline) and model."""
    out = run_adb(adb, "devices", "-l", timeout=8).stdout.decode(errors="replace")
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        # Lines like "* daemon started successfully" aren't devices.
        if len(parts) < 2 or line.startswith("*"):
            continue
        details = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
        devices.append({
            "serial": parts[0],
            "state": parts[1],
            "model": details.get("model", "").replace("_", " ") or parts[0],
            "wifi": is_wifi(parts[0]),
        })
    return devices


def is_wifi(serial):
    """An address (192.168.1.20:5555) or a wireless-debugging name, rather than a USB serial."""
    return ":" in serial or "._adb-tls-connect" in serial


def adb_text(result):
    return (result.stdout + result.stderr).decode(errors="replace").strip()


def wifi_addresses(adb):
    """Paired phones advertising wireless debugging on the network (Android 11+), as IP:port."""
    out = adb_text(run_adb(adb, "mdns", "services", timeout=8))
    return re.findall(r"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+:\d+)", out)


def connect_wifi(adb, address):
    """True if adb is connected to the phone at address (already connected counts)."""
    out = adb_text(run_adb(adb, "connect", address, timeout=10)).lower()
    return "connected to" in out and "cannot" not in out and "failed" not in out


def switch_to_wifi(adb, serial):
    """A USB phone carries on over Wi-Fi: its Wi-Fi address is read, adb on it listens on WIFI_PORT, and adb
    connects there. Returns the address."""
    route = adb_text(run_adb(adb, "-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0", timeout=8))
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", route)
    if not match:
        raise RuntimeError("The phone has no Wi-Fi address. Turn its Wi-Fi on, on the same network as this PC.")
    address = f"{match.group(1)}:{WIFI_PORT}"
    run_adb(adb, "-s", serial, "tcpip", str(WIFI_PORT), timeout=10)
    # adb on the phone restarts to listen on the network.
    for _ in range(8):
        time.sleep(0.8)
        if connect_wifi(adb, address):
            return address
    raise RuntimeError(f"Couldn't reach the phone at {address}. Check both are on the same Wi-Fi.")


def pair_wifi(adb, address, code):
    """Pairs with a phone's 'Pair device with pairing code' screen, then connects to it if it's advertised."""
    out = adb_text(run_adb(adb, "pair", address, code, timeout=30))
    if "successfully paired" not in out.lower():
        raise RuntimeError(out.splitlines()[-1] if out else "Pairing failed.")
    host = address.rsplit(":", 1)[0]
    for _ in range(6):
        time.sleep(1)
        for found in wifi_addresses(adb):
            if found.startswith(host + ":") and connect_wifi(adb, found):
                return found
    return None


def capture(adb, serial):
    """The phone's screen as an RGB image, as fast as the phone allows.

    Raw pixels squeezed by gzip on the phone, read straight from the adb server's socket, beat both the phone's
    own PNG encoder and sending raw pixels unpacked: about 0.3s against 0.45s and 1s on a Galaxy S22. A phone
    without gzip, or an adb server the socket can't reach, falls back to a PNG through adb.exe.
    """
    try:
        packed = adb_exec(serial, "screencap | gzip -1")
        if packed[:2] == b"\x1f\x8b":
            image = raw_image(zlib.decompress(packed, 16 + zlib.MAX_WBITS))
            if image is not None:
                return image
    except (OSError, zlib.error):
        pass
    return png_capture(adb, serial)


def adb_exec(serial, command, timeout=15):
    """Runs a command on the phone through the adb server's own socket, skipping a new adb.exe for every shot."""
    with socket.create_connection(("127.0.0.1", ADB_PORT), timeout=timeout) as sock:
        for request in (f"host:transport:{serial}", f"exec:{command}"):
            sock.sendall(f"{len(request):04x}{request}".encode())
            status = read_exact(sock, 4)
            if status != b"OKAY":
                length = int(read_exact(sock, 4), 16)
                raise OSError(read_exact(sock, length).decode(errors="replace"))
        chunks = []
        while chunk := sock.recv(1 << 20):
            chunks.append(chunk)
    return b"".join(chunks)


def read_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("adb server closed the connection")
        data += chunk
    return data


def raw_image(raw):
    """screencap's raw output: a small header (width, height, format and, on newer phones, colour space) then pixels."""
    if len(raw) < 12:
        return None
    width, height, pixel_format = struct.unpack_from("<III", raw)
    mode = RAW_MODES.get(pixel_format)
    size = width * height * 4
    if mode is None or len(raw) < size + 12:
        return None
    return Image.frombuffer("RGBA", (width, height), raw[len(raw) - size:], "raw", mode, 0, 1).convert("RGB")


def png_capture(adb, serial):
    result = run_adb(adb, "-s", serial, "exec-out", "screencap", "-p", timeout=20)
    if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG"):
        message = result.stderr.decode(errors="replace").strip() or "The phone didn't send a screenshot."
        raise RuntimeError(message.splitlines()[-1])
    return Image.open(io.BytesIO(result.stdout)).convert("RGB")


# ---------------------------------------------------------------- Windows: clipboard and shortcut

CF_DIB = 8
GMEM_MOVEABLE = 0x0002
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Handles are pointer-sized on 64-bit Windows, so every signature is spelled out to keep them whole.
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
MONITOR_DEFAULTTONEAREST = 2


def window_monitor(root):
    return user32.MonitorFromWindow(user32.GetParent(root.winfo_id()), MONITOR_DEFAULTTONEAREST)


def work_area(root):
    """The usable part of the screen the window is on, taskbar left out, as (left, top, right, bottom)."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(window_monitor(root), ctypes.byref(info)):
        area = info.rcWork
        return area.left, area.top, area.right, area.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def clipboard_formats(image):
    """The screenshot as a bitmap every app can paste. It's lossless, and skipping a PNG copy keeps it quick."""
    bitmap = io.BytesIO()
    image.save(bitmap, "BMP")
    # A clipboard DIB is a .bmp file without its 14-byte file header.
    return [(CF_DIB, bitmap.getvalue()[14:])]


def set_clipboard(formats):
    # Another app may be holding the clipboard for a moment, so opening it is retried briefly.
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        raise RuntimeError("The clipboard is busy. Try again.")
    try:
        user32.EmptyClipboard()
        for kind, data in formats:
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            pointer = kernel32.GlobalLock(handle)
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            # Once Windows accepts it the clipboard owns the memory; only a refusal leaves it to us to free.
            if not user32.SetClipboardData(kind, handle):
                kernel32.GlobalFree(handle)
                raise RuntimeError("Windows wouldn't take the image.")
    finally:
        user32.CloseClipboard()


def create_desktop_shortcut():
    """Puts a screenadb shortcut, with its icon, on the desktop (wherever Windows keeps it, OneDrive included)."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.is_file() else Path(sys.executable)

    def quoted(value):
        # Inside PowerShell's single quotes, a quote is written twice.
        return "'" + str(value).replace("'", "''") + "'"

    script = "; ".join([
        "$desktop = [Environment]::GetFolderPath('Desktop')",
        "$path = Join-Path $desktop 'screenadb.lnk'",
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)",
        f"$link.TargetPath = {quoted(target)}",
        f"$link.Arguments = {quoted(chr(34) + str(HERE / 'screenadb.pyw') + chr(34))}",
        f"$link.WorkingDirectory = {quoted(HERE)}",
        f"$link.IconLocation = {quoted(str(ICON_ICO) + ',0')}",
        "$link.Description = 'Phone screenshots over adb'",
        "$link.Save()",
        "Write-Output $path",
    ])
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=30, creationflags=NO_WINDOW)
    path = result.stdout.decode(errors="replace").strip()
    if result.returncode != 0 or not path:
        raise RuntimeError(result.stderr.decode(errors="replace").strip().splitlines()[-1] if result.stderr
                           else "Windows didn't create the shortcut.")
    return Path(path)


# ---------------------------------------------------------------- drawing helpers


def rounded(size, radius, fill, outline=None, width=0, scale=4):
    """A smooth rounded rectangle image. Tk draws jagged curves, so Pillow draws it 4x larger and shrinks it."""
    w, h = size
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=fill,
        outline=outline, width=width * scale,
    )
    return big.resize((w, h), Image.LANCZOS)


def round_corners(image, radius):
    mask = Image.new("L", (image.width * 4, image.height * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius=radius * 4, fill=255)
    shaped = image.convert("RGBA")
    shaped.putalpha(mask.resize(image.size, Image.LANCZOS))
    return shaped


def mix(start, end, amount):
    """The colour amount of the way from start to end, both as #RRGGBB. Tk has no transparency, so fades blend."""
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def icon_face(kind, size, fill, ink, outline=None, scale=4):
    """A round icon button's face: a filled circle with a gear or a push pin on it, drawn large and shrunk so it's
    smooth at any size."""
    big = size * scale
    face = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(face).ellipse((0, 0, big - 1, big - 1), fill=fill, outline=outline, width=scale if outline else 0)
    glyph = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)
    middle = big / 2
    if kind == "gear":
        body, reach, hole = big * 0.19, big * 0.27, big * 0.08
        for tooth in range(8):
            angle = math.pi / 4 * tooth
            along = (math.cos(angle), math.sin(angle))
            across = (-along[1], along[0])
            points = []
            for distance, half in ((body * 0.8, big * 0.065), (reach, big * 0.05), (reach, -big * 0.05),
                                   (body * 0.8, -big * 0.065)):
                points.append((middle + along[0] * distance + across[0] * half,
                               middle + along[1] * distance + across[1] * half))
            draw.polygon(points, fill=ink)
        draw.ellipse((middle - body, middle - body, middle + body, middle + body), fill=ink)
        # Drawing in clear paint punches the hole straight through.
        draw.ellipse((middle - hole, middle - hole, middle + hole, middle + hole), fill=(0, 0, 0, 0))
    else:
        unit = big / 100
        draw.rounded_rectangle((middle - 11 * unit, middle - 27 * unit, middle + 11 * unit, middle - 18 * unit),
                               radius=4 * unit, fill=ink)
        draw.polygon([(middle - 7 * unit, middle - 18 * unit), (middle + 7 * unit, middle - 18 * unit),
                      (middle + 12 * unit, middle + 3 * unit), (middle - 12 * unit, middle + 3 * unit)], fill=ink)
        draw.rounded_rectangle((middle - 18 * unit, middle + 1 * unit, middle + 18 * unit, middle + 8 * unit),
                               radius=3 * unit, fill=ink)
        draw.line((middle, middle + 8 * unit, middle, middle + 28 * unit), fill=ink, width=round(4 * unit))
        # Tilted, the way a pin pressed into a board sits.
        glyph = glyph.rotate(-40, resample=Image.BICUBIC, center=(middle, middle))
    return Image.alpha_composite(face, glyph).resize((size, size), Image.LANCZOS)


class Tooltip:
    """A small label that appears under a control once the pointer has rested on it for a moment."""

    def __init__(self, widget, text, px):
        self.widget = widget
        self.text = text
        self.px = px
        self.window = None
        self.label = None
        self.pending = None

    def schedule(self):
        self.cancel()
        self.pending = self.widget.after(450, self.show)

    def cancel(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None

    def show(self):
        self.pending = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.label = tk.Label(self.window, text=self.text, bg=C["surface"], fg=C["text"], font=("Segoe UI", 9),
                              padx=self.px(10), pady=self.px(5), highlightthickness=1,
                              highlightbackground=C["border"])
        self.label.pack()
        self.window.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - self.window.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + self.px(6)
        self.window.geometry(f"+{x}+{y}")

    def hide(self):
        self.cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def set_text(self, text):
        self.text = text
        if self.label is not None and self.window is not None:
            self.label.configure(text=text)


class IconButton(tk.Canvas):
    """A round icon button (gear, pin) that lights up under the pointer, shrinks a little when pressed, shows a
    tooltip, and can be switched on (coral) for things like keep-on-top."""

    def __init__(self, parent, kind, tip, command, px, active=False):
        self.size = px(36)
        super().__init__(parent, width=self.size, height=self.size, bg=C["bg"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.kind = kind
        self.command = command
        self.active = active
        self.hovering = False
        self.pressing = False
        self.faces = {}
        self.tooltip = Tooltip(self, tip, px)
        self.bind("<Enter>", lambda _: (self.set_flags(hovering=True), self.tooltip.schedule()))
        self.bind("<Leave>", lambda _: (self.set_flags(hovering=False, pressing=False), self.tooltip.hide()))
        self.bind("<ButtonPress-1>", lambda _: (self.set_flags(pressing=True), self.tooltip.hide()))
        self.bind("<ButtonRelease-1>", self.release)
        self.draw()

    def set_flags(self, **flags):
        for name, value in flags.items():
            setattr(self, name, value)
        self.draw()

    def release(self, event):
        was_pressing = self.pressing
        self.set_flags(pressing=False)
        if was_pressing and 0 <= event.x <= self.size and 0 <= event.y <= self.size:
            self.command()

    def set_active(self, active, tip=None):
        self.active = active
        if tip is not None:
            self.tooltip.set_text(tip)
        self.draw()

    def draw(self):
        if self.active:
            fill = CORAL_PRESSED if self.pressing else CORAL_HOVER if self.hovering else CORAL
            ink, outline = "#FFFFFF", None
        else:
            fill = C["border"] if self.pressing else C["surface_hover"] if self.hovering else C["surface"]
            ink, outline = (C["text"] if self.hovering or self.pressing else C["muted"]), C["border"]
        size = round(self.size * 0.9) if self.pressing else self.size
        key = (fill, ink, outline, size)
        if key not in self.faces:
            self.faces[key] = ImageTk.PhotoImage(icon_face(self.kind, size, fill, ink, outline))
        self.delete("all")
        self.create_image(self.size // 2, self.size // 2, image=self.faces[key])


class TabSwitch(tk.Canvas):
    """Options side by side on one rounded track, with a coral thumb that glides to the one picked, like the
    Real / Demo switch in AccuPilot Pro. Labels shade from grey to white as the thumb passes under them."""

    GLIDE_SECONDS = 0.28

    def __init__(self, parent, options, selected, on_change, height, px):
        super().__init__(parent, width=px(300), height=height, bg=C["bg"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.options = options
        self.index = [value for value, _ in options].index(selected)
        # Where the thumb is, in options: 0 is the first, 1 the second; fractions while it glides.
        self.position = float(self.index)
        self.on_change = on_change
        self.px = px
        self.hover = None
        self.images = {}
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Motion>", self.track_hover)
        self.bind("<Leave>", lambda _: self.set_hover(None))
        self.bind("<ButtonRelease-1>", self.pick)

    def slot(self, x):
        return min(len(self.options) - 1, max(0, int(x / max(1, self.winfo_width()) * len(self.options))))

    def track_hover(self, event):
        self.set_hover(self.slot(event.x))

    def set_hover(self, index):
        if index != self.hover:
            self.hover = index
            self.draw()

    def pick(self, event):
        index = self.slot(event.x)
        if index == self.index:
            return
        start = self.position
        self.index = index
        began = time.perf_counter()

        def glide():
            if not self.winfo_exists():
                return
            progress = min(1.0, (time.perf_counter() - began) / self.GLIDE_SECONDS)
            self.position = start + (index - start) * (1 - (1 - progress) ** 3)
            self.draw()
            if progress < 1:
                self.after(14, glide)
            else:
                self.on_change(self.options[index][0])

        glide()

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        inset = self.px(4)
        segment = (w - 2 * inset) / len(self.options)
        track_key = ("track", w, h, C["surface"], C["border"])
        if track_key not in self.images:
            self.images[track_key] = ImageTk.PhotoImage(
                rounded((w, h), h // 2, C["surface"], C["border"], width=max(1, self.px(1))))
        thumb_size = (round(segment), h - 2 * inset)
        thumb_key = ("thumb", thumb_size)
        if thumb_key not in self.images:
            self.images[thumb_key] = ImageTk.PhotoImage(rounded(thumb_size, thumb_size[1] // 2, CORAL))
        self.delete("all")
        self.create_image(0, 0, image=self.images[track_key], anchor="nw")
        self.create_image(round(inset + self.position * segment), inset, image=self.images[thumb_key], anchor="nw")
        for index, (_, label) in enumerate(self.options):
            under = max(0.0, 1 - abs(self.position - index))
            resting = C["text"] if self.hover == index else C["muted"]
            self.create_text(inset + segment * (index + 0.5), h / 2, text=label, fill=mix(resting, "#FFFFFF", under),
                             font=("Segoe UI Semibold", 10))


class Scroller(tk.Frame):
    """One of the window's views. When its content is taller than the window can be (a small screen), it scrolls with
    the mouse wheel and a slim bar on the right shows where you are; when everything fits, it's just a plain page.
    Build the view's widgets inside .content."""

    def __init__(self, parent, px):
        super().__init__(parent, bg=C["bg"])
        self.px = px
        self.canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0, yscrollincrement=px(24))
        self.canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=C["bg"], padx=px(20), pady=px(20))
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        # The bar sits in the right-hand margin, so it never covers anything.
        self.bar = tk.Frame(self, bg=C["border"], width=px(4))
        self.settle = None
        self.content.bind("<Configure>", lambda _: self.refresh())
        self.canvas.bind("<Configure>", self.resized)

    def resized(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)
        self.refresh()

    def overflow(self):
        return self.content.winfo_reqheight() - self.canvas.winfo_height()

    def refresh(self):
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        self.place_bar()

    def scroll(self, steps):
        if self.overflow() > 1:
            self.canvas.yview_scroll(steps, "units")
            self.place_bar(active=True)

    def place_bar(self, active=False):
        if self.overflow() <= 1:
            self.bar.place_forget()
            self.canvas.yview_moveto(0)
            return
        first, last = self.canvas.yview()
        inset = self.px(6)
        track = self.canvas.winfo_height() - 2 * inset
        self.bar.place(relx=1, x=-self.px(7), y=inset + first * track, width=self.px(4),
                       height=max(self.px(24), (last - first) * track), anchor="ne")
        # Brighter while scrolling, then it settles back to a quiet line.
        self.bar.configure(bg=C["muted"] if active else C["border"])
        if active:
            if self.settle is not None:
                self.after_cancel(self.settle)
            self.settle = self.after(700, lambda: self.bar.winfo_exists() and self.bar.configure(bg=C["border"]))


class PillButton(tk.Canvas):
    """A rounded button with hover and press states and a small key hint on its right. Pressing sinks it a little;
    a click springs it back with a short settle and a soft glow that fades, so it feels pressed, not just recoloured."""

    PRESS_SECONDS = 0.09
    SPRING_SECONDS = 0.45
    PRESSED_SIZE = 0.94

    def __init__(self, parent, text, command, primary, hint, height, px, width=None):
        # The width is only what it asks for; packed or gridded to fill, it stretches to its row.
        super().__init__(parent, width=width or px(300), height=height, bg=C["bg"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.text = text
        self.command = command
        self.primary = primary
        self.hint = hint
        self.px = px
        self.enabled = True
        self.hovering = False
        self.pressing = False
        self.images = {}
        # How big the button is drawn (1 at rest) and how much of a click's glow still shows (0 to 1).
        self.size = 1.0
        self.glow = 0.0
        self.motion = None
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Enter>", lambda _: self.set_flags(hovering=True))
        self.bind("<Leave>", self.leave)
        self.bind("<ButtonPress-1>", lambda _: self.press())
        self.bind("<ButtonRelease-1>", self.release)

    def set_flags(self, **flags):
        for name, value in flags.items():
            setattr(self, name, value)
        self.draw()

    def press(self):
        self.set_flags(pressing=True)
        if self.enabled:
            self.ease_to(self.PRESSED_SIZE)

    def leave(self, _):
        was_pressing = self.pressing
        self.set_flags(hovering=False, pressing=False)
        if was_pressing:
            self.ease_to(1.0)

    def release(self, event):
        was_pressing = self.pressing
        self.set_flags(pressing=False)
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if was_pressing and inside and self.enabled:
            self.spring()
            self.command()
        else:
            self.ease_to(1.0)

    def pulse(self):
        """A key press gets the same press-and-spring as a click."""
        self.size = self.PRESSED_SIZE
        self.spring()

    def ease_to(self, target):
        start, began = self.size, time.perf_counter()
        self.stop_motion()

        def frame():
            if not self.winfo_exists():
                return
            progress = min(1.0, (time.perf_counter() - began) / self.PRESS_SECONDS)
            self.size = start + (target - start) * (1 - (1 - progress) ** 2)
            self.draw()
            if progress < 1:
                self.motion = self.after(12, frame)

        frame()

    def spring(self):
        """The click: the button bounces back from its pressed size and settles, never growing past its own size,
        while a glow flashes over it and fades."""
        start, began = self.size, time.perf_counter()
        self.stop_motion()

        def frame():
            if not self.winfo_exists():
                return
            progress = min(1.0, (time.perf_counter() - began) / self.SPRING_SECONDS)
            self.size = 1 - (1 - start) * math.exp(-5 * progress) * abs(math.cos(2.5 * math.pi * progress))
            self.glow = (1 - progress) ** 2
            if progress >= 1:
                self.size, self.glow = 1.0, 0.0
            self.draw()
            if progress < 1:
                self.motion = self.after(12, frame)

        frame()

    def stop_motion(self):
        if self.motion is not None:
            self.after_cancel(self.motion)
            self.motion = None

    def configure_button(self, text=None, enabled=None, primary=None):
        if text is not None:
            self.text = text
        if primary is not None:
            self.primary = primary
        if enabled is not None:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def colors(self):
        if self.primary:
            if not self.enabled:
                return C["coral_disabled"], None, C["coral_disabled_text"]
            if self.pressing:
                return CORAL_PRESSED, None, "#FFFFFF"
            return (CORAL_HOVER if self.hovering else CORAL), None, "#FFFFFF"
        if not self.enabled:
            return C["surface"], C["border"], C["muted"]
        return (C["surface_hover"] if self.hovering or self.pressing else C["surface"]), C["border"], C["text"]

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        fill, outline, text_color = self.colors()
        moving = self.size < 0.999 or self.glow > 0.01
        # While moving, sizes snap to even pixels and are drawn a little less finely, so a press only ever needs a
        # handful of quick drawings; at rest the button is drawn at full quality.
        pill_w = max(2, round(w * self.size / 2) * 2) if moving else w
        pill_h = max(2, round(h * self.size / 2) * 2) if moving else h
        key = (pill_w, pill_h, fill, outline, moving)
        if key not in self.images:
            self.images[key] = ImageTk.PhotoImage(rounded(
                (pill_w, pill_h), pill_h // 2, fill, outline, width=max(1, self.px(1)) if outline else 0,
                scale=2 if moving else 4,
            ))
        self.delete("all")
        middle_x, middle_y = w / 2, h / 2
        self.create_image(middle_x, middle_y, image=self.images[key])
        level = round(self.glow * 5)
        if level:
            glow_key = ("glow", pill_w, pill_h, level, self.primary)
            if glow_key not in self.images:
                # White light on the coral button; on the plain one white wouldn't show, so it glows coral.
                tint = (255, 255, 255, 24 * level) if self.primary else (255, 68, 79, 16 * level)
                self.images[glow_key] = ImageTk.PhotoImage(rounded((pill_w, pill_h), pill_h // 2, tint, scale=2))
            self.create_image(middle_x, middle_y, image=self.images[glow_key])
        self.create_text(middle_x, middle_y, text=self.text, fill=text_color, font=("Segoe UI Semibold", 11))
        if self.hint:
            size = self.px(22)
            badge_key = ("badge", size, self.primary, C["text"])
            if badge_key not in self.images:
                light = self.primary or C["text"] == "#FFFFFF"
                tint = (255, 255, 255, 46 if self.primary else 22) if light else (24, 28, 37, 22)
                self.images[badge_key] = ImageTk.PhotoImage(rounded((size, size), self.px(6), tint))
            x = middle_x + pill_w / 2 - self.px(18) - size / 2
            self.create_image(x, middle_y, image=self.images[badge_key])
            self.create_text(x, middle_y, text=self.hint, fill=text_color, font=("Segoe UI Semibold", 8))


# ---------------------------------------------------------------- app


class ScreenAdb:
    def __init__(self, root):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96
        self.events = queue.Queue()
        self.settings = load_settings()
        C.update(THEMES[self.settings["theme"]])
        self.adb = None
        self.version = ""
        self.devices = []
        self.serial = None
        self.polling = False
        self.busy = False
        self.installing = False
        self.install_progress = 0.0
        self.install_label = None
        self.last_image = None
        self.last_path = None
        self.last_thumb = None
        self.preview_image = None
        self.flash_frames = []
        # What the window shows, kept so it can be rebuilt in another theme exactly as it was.
        self.adb_state = ("muted", "Checking…")
        self.phone_state = ("muted", "Checking…")
        self.message_state = ("", "muted")
        self.setup_key = None
        self.view = "main"
        # The preview's height: full size on roomy screens, smaller when the screen is short.
        self.preview_height = self.px(250)
        self.monitor = None
        self.monitor_check = None
        # Background shots while the pointer is over the window: the newest (image, when it was started, which
        # phone), whether one is on its way, and a click waiting for it.
        self.hovering = False
        self.latest = None
        self.fetching = False
        self.waiting = None
        # Wi-Fi: when phones were last looked for, and a switch or pairing under way.
        self.reconnect_at = 0.0
        self.wifi_busy = False
        self.wifi_state = ("", "muted")

        root.title("screenadb")
        root.resizable(False, False)
        if ICON_ICO.exists():
            root.iconbitmap(default=str(ICON_ICO))
        self.build()
        self.bind_keys()
        if self.settings["pinned"]:
            root.attributes("-topmost", True)
        self.restore_position()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<MouseWheel>", self.wheel)
        root.bind("<Configure>", self.moved)
        # Once the window is really on screen, its frame size and monitor are known, so it's fitted again.
        root.after(80, self.fit)
        root.after(PUMP_MS, self.pump)
        root.after(POINTER_MS, self.watch_pointer)
        self.poll()

    def px(self, value):
        return int(round(value * self.scale))

    # ------------------------------------------------------------ layout

    def build(self):
        self.root.configure(bg=C["bg"])
        self.stage = tk.Frame(self.root, bg=C["bg"])
        self.stage.pack(fill="both", expand=True)
        self.main = Scroller(self.stage, self.px)
        self.settings_view = Scroller(self.stage, self.px)
        self.build_main(self.main.content)
        self.build_settings(self.settings_view.content)
        self.restore()
        self.fit()

    def rebuild(self):
        """A theme change: everything is built again in the new colours, showing the same things as before."""
        self.stage.destroy()
        self.build()
        dark_title_bar(self.root, self.settings["theme"] == "dark")

    def build_main(self, frame):
        header = tk.Frame(frame, bg=C["bg"])
        header.pack(fill="x")
        if ICON_PNG.exists():
            self.logo = ImageTk.PhotoImage(Image.open(ICON_PNG).resize((self.px(40), self.px(40)), Image.LANCZOS))
            tk.Label(header, image=self.logo, bg=C["bg"]).pack(side="left")
        titles = tk.Frame(header, bg=C["bg"])
        titles.pack(side="left", padx=(self.px(12), 0))
        tk.Label(titles, text="screenadb", bg=C["bg"], fg=C["text"], font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tk.Label(titles, text="Phone screenshots over adb", bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w")
        IconButton(header, "gear", "Settings", self.open_settings, self.px).pack(side="right")
        self.pin_button = IconButton(header, "pin", "", self.toggle_pin, self.px)
        self.pin_button.pack(side="right", padx=(0, self.px(8)))
        self.show_pin()

        status = tk.Frame(frame, bg=C["bg"])
        status.pack(fill="x", pady=(self.px(20), self.px(6)))
        status.columnconfigure(1, weight=1)
        self.adb_dot, self.adb_text = self.status_row(status, 0, "ADB")
        self.phone_dot, self.phone_text = self.status_row(status, 1, "Phone")
        # With more than one phone plugged in, clicking the phone line switches between them.
        for widget in (self.phone_dot, self.phone_text):
            widget.bind("<Button-1>", lambda _: self.next_device())

        # Guidance for getting set up (installing adb, turning on USB debugging) appears here when it's needed.
        self.setup = tk.Frame(frame, bg=C["bg"])

        self.copy_button = PillButton(frame, "Copy screenshot", lambda: self.take(copy=True), True, "C",
                                      self.px(50), self.px)
        self.copy_button.pack(fill="x", pady=(self.px(14), 0))
        self.save_button = PillButton(frame, "Take screenshot", lambda: self.take(copy=False), False, "S",
                                      self.px(50), self.px)
        self.save_button.pack(fill="x", pady=(self.px(10), 0))

        self.preview = tk.Label(frame, bg=C["bg"], cursor="hand2")
        self.preview.pack(pady=(self.px(20), 0))
        self.preview.bind("<Button-1>", lambda _: self.open_last())

        self.message = tk.Label(frame, text="", bg=C["bg"], fg=C["muted"], font=("Segoe UI", 9),
                                wraplength=self.px(300))
        self.message.pack(pady=(self.px(12), 0))
        self.link(frame, "Open shots folder  ·  O", self.open_folder, underline=True).pack(pady=(self.px(4), 0))
        self.link(frame, "Made by Someless Tricks", self.open_settings, font=("Segoe UI", 8)).pack(
            pady=(self.px(10), 0))

    def build_settings(self, frame):
        top = tk.Frame(frame, bg=C["bg"])
        top.pack(fill="x")
        self.link(top, "←  Back", self.close_settings, font=("Segoe UI Semibold", 10)).pack(side="left")
        tk.Label(frame, text="Settings", bg=C["bg"], fg=C["text"], font=("Segoe UI Semibold", 16)).pack(
            anchor="w", pady=(self.px(14), 0))

        self.section(frame, "Appearance")
        TabSwitch(frame, [("dark", "Dark"), ("light", "Light")], self.settings["theme"], self.set_theme,
                  self.px(46), self.px).pack(fill="x")

        self.section(frame, "Desktop shortcut")
        PillButton(frame, "Create desktop shortcut", self.make_shortcut, False, "", self.px(42), self.px).pack(fill="x")
        self.shortcut_note = self.note(frame, "Adds screenadb, with its icon, to your desktop.")

        self.section(frame, "Wireless")
        self.note(frame, "Phones used over Wi-Fi reconnect by themselves whenever none is ready. The phone and this PC "
                         "need the same Wi-Fi.")
        self.wifi_button = PillButton(frame, "Switch this phone to Wi-Fi", self.go_wifi, False, "", self.px(42),
                                      self.px)
        self.wifi_button.pack(fill="x", pady=(self.px(8), 0))
        self.note(frame, "Android 11+, no cable: Developer options › Wireless debugging › Pair device with pairing "
                         "code, then enter what it shows.")
        fields = tk.Frame(frame, bg=C["bg"])
        fields.pack(fill="x", pady=(self.px(8), 0))
        fields.columnconfigure(0, weight=3)
        fields.columnconfigure(1, weight=2)
        self.pair_address = self.field(fields, "IP address & port", 0)
        self.pair_code = self.field(fields, "Pairing code", 1)
        self.pair_button = PillButton(frame, "Pair", self.pair, False, "", self.px(42), self.px)
        self.pair_button.pack(fill="x", pady=(self.px(8), 0))
        self.wifi_note = self.note(frame, self.wifi_state[0])
        self.wifi_note.configure(fg=C[self.wifi_state[1]])

        self.section(frame, "adb")
        self.adb_note = self.note(frame, "")
        self.update_button = PillButton(frame, "Download latest adb", self.install_adb, False, "",
                                        self.px(42), self.px)
        self.update_button.pack(fill="x", pady=(self.px(8), 0))

        self.section(frame, "About")
        about = tk.Frame(frame, bg=C["bg"])
        about.pack(fill="x")
        if MAKER_LOGO.exists():
            logo = Image.open(MAKER_LOGO)
            width = self.px(210)
            self.maker_logo = ImageTk.PhotoImage(logo.resize((width, round(logo.height * width / logo.width)),
                                                             Image.LANCZOS))
            tk.Label(about, image=self.maker_logo, bg=C["bg"]).pack()
        tk.Label(about, text="Made by Someless Tricks", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI Semibold", 11)).pack(pady=(self.px(6), 0))
        tk.Label(about, text=f"screenadb {VERSION}  ·  Phone screenshots over adb", bg=C["bg"], fg=C["muted"],
                 font=("Segoe UI", 9)).pack()

    def section(self, parent, title):
        tk.Label(parent, text=title.upper(), bg=C["bg"], fg=CORAL, font=("Segoe UI Semibold", 8)).pack(
            anchor="w", pady=(self.px(18), self.px(6)))

    def note(self, parent, text):
        label = tk.Label(parent, text=text, bg=C["bg"], fg=C["muted"], font=("Segoe UI", 9), justify="left",
                         anchor="w", wraplength=self.px(300))
        label.pack(fill="x", pady=(self.px(6), 0))
        return label

    def link(self, parent, text, action, underline=False, font=("Segoe UI", 9)):
        """Text that acts as a button, brightening under the pointer."""
        if underline:
            font = (*font[:2], "underline")
        label = tk.Label(parent, text=text, bg=C["bg"], fg=C["muted"], font=font, cursor="hand2")
        label.bind("<Button-1>", lambda _: action())
        label.bind("<Enter>", lambda _: label.configure(fg=C["text"]))
        label.bind("<Leave>", lambda _: label.configure(fg=C["muted"]))
        return label

    def field(self, parent, label, column):
        cell = tk.Frame(parent, bg=C["bg"])
        cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else self.px(8), 0))
        tk.Label(cell, text=label, bg=C["bg"], fg=C["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        entry = tk.Entry(cell, bg=C["surface"], fg=C["text"], insertbackground=C["text"], relief="flat",
                         font=("Segoe UI", 10), highlightthickness=1, highlightbackground=C["border"],
                         highlightcolor=CORAL)
        entry.pack(fill="x", ipady=self.px(6), pady=(self.px(3), 0))
        entry.bind("<Return>", lambda _: self.pair())
        return entry

    def status_row(self, parent, row, title):
        tk.Label(parent, text=title, bg=C["bg"], fg=C["muted"], font=("Segoe UI", 10), width=6, anchor="w").grid(
            row=row, column=0, sticky="w", pady=self.px(3))
        line = tk.Frame(parent, bg=C["bg"])
        line.grid(row=row, column=1, sticky="w")
        dot = tk.Label(line, text="●", bg=C["bg"], fg=C["muted"], font=("Segoe UI", 10))
        dot.pack(side="left")
        text = tk.Label(line, text="Checking…", bg=C["bg"], fg=C["text"], font=("Segoe UI", 10))
        text.pack(side="left", padx=(self.px(6), 0))
        return dot, text

    def restore(self):
        """Puts the last known state back after building, so a theme change shows exactly what was there."""
        self.set_status(self.adb_dot, self.adb_text, *self.adb_state)
        self.set_status(self.phone_dot, self.phone_text, *self.phone_state)
        self.say(*self.message_state)
        self.setup_key = None
        self.render_setup()
        self.render_adb_note()
        if self.last_thumb is not None:
            self.show_preview(self.last_thumb, flash=False)
        else:
            self.show_placeholder()
        self.refresh_buttons()

    def bind_keys(self):
        # Keys typed into a field (the pairing ones) are just typing, not shortcuts.
        def shortcut(action):
            return lambda event: None if isinstance(event.widget, tk.Entry) else action()

        for key in ("<KeyPress-c>", "<KeyPress-C>", "<Control-c>"):
            self.root.bind(key, shortcut(lambda: self.key_take(copy=True)))
        for key in ("<KeyPress-s>", "<KeyPress-S>", "<Control-s>"):
            self.root.bind(key, shortcut(lambda: self.key_take(copy=False)))
        self.root.bind("<KeyPress-o>", shortcut(self.open_folder))
        self.root.bind("<KeyPress-p>", shortcut(self.toggle_pin))
        self.root.bind("<Escape>", lambda _: self.close_settings())

    # ------------------------------------------------------------ views

    def fit(self):
        """Sizes the window to the main view without ever running past the screen it's on (taskbar left clear).
        On a short screen the preview shrinks first; whatever still doesn't fit, Settings included, scrolls.
        The width only ever grows, so the window doesn't jump sideways when guidance comes and goes."""
        self.root.update_idletasks()
        left, top, right, bottom = work_area(self.root)
        mapped = self.root.winfo_ismapped()
        # The title bar and borders, measured once the window is up; a fair guess before that.
        title = self.root.winfo_rooty() - self.root.winfo_y() if mapped else self.px(32)
        edge = self.root.winfo_rootx() - self.root.winfo_x() if mapped else self.px(8)
        room_height = bottom - top - title - edge - self.px(8)
        room_width = right - left - 2 * edge - self.px(8)

        if self.preview.winfo_manager():
            # A second pass corrects for the few pixels of padding the preview carries around its picture.
            for _ in range(2):
                others = self.main.content.winfo_reqheight() - self.preview_height
                wanted = min(self.px(250), max(self.px(90), room_height - others))
                if wanted == self.preview_height:
                    break
                self.preview_height = wanted
                self.redraw_preview()
                self.root.update_idletasks()

        self.stage_width = min(room_width, max(getattr(self, "stage_width", 0), self.main.content.winfo_reqwidth(),
                                               self.settings_view.content.winfo_reqwidth(), self.px(340)))
        height = min(room_height, self.main.content.winfo_reqheight())
        self.stage.configure(width=self.stage_width, height=height)
        self.main.place(x=0 if self.view == "main" else -self.stage_width, y=0, relwidth=1, relheight=1)
        self.settings_view.place(x=0 if self.view == "settings" else self.stage_width, y=0, relwidth=1, relheight=1)
        if mapped:
            self.root.update_idletasks()
            self.keep_on_screen(left, top, right, bottom)

    def keep_on_screen(self, left, top, right, bottom):
        """Nudges the window back inside the screen if any of it hangs off an edge."""
        x, y = self.root.winfo_x(), self.root.winfo_y()
        edge = self.root.winfo_rootx() - x
        outer_width = self.root.winfo_width() + 2 * edge
        outer_height = self.root.winfo_height() + (self.root.winfo_rooty() - y) + edge
        new_x = max(left, min(x, right - outer_width))
        new_y = max(top, min(y, bottom - outer_height))
        if (new_x, new_y) != (x, y):
            self.root.geometry(f"+{new_x}+{new_y}")

    def moved(self, event):
        # Dragged onto another screen, the window refits to that screen's size. Checked once the drag settles.
        if event.widget is not self.root:
            return
        if self.monitor_check is not None:
            self.root.after_cancel(self.monitor_check)
        self.monitor_check = self.root.after(250, self.check_monitor)

    def check_monitor(self):
        self.monitor_check = None
        monitor = window_monitor(self.root)
        if monitor != self.monitor:
            self.monitor = monitor
            self.fit()

    def wheel(self, event):
        view = self.main if self.view == "main" else self.settings_view
        view.scroll(-1 if event.delta > 0 else 1)

    def slide(self, to):
        """Settings slides in from the right over the main view, and back out the same way."""
        if to == self.view:
            return
        self.view = to
        width = self.stage.winfo_width()
        frames = 14

        def step(index):
            progress = 1 - (1 - index / frames) ** 3
            offset = progress if to == "settings" else 1 - progress
            self.main.place_configure(x=round(-width * 0.3 * offset))
            self.settings_view.place_configure(x=round(width * (1 - offset)))
            if offset > 0:
                self.settings_view.lift()
            if index < frames:
                self.root.after(14, lambda: step(index + 1))
            elif to == "main":
                self.main.lift()

        step(1)

    def open_settings(self):
        self.render_adb_note()
        self.slide("settings")

    def close_settings(self):
        self.slide("main")

    # ------------------------------------------------------------ settings actions

    def set_theme(self, name):
        """Called once the switch's thumb has glided over. The window dims for a moment while everything is
        redrawn in the new colours, then brightens again, so the change reads as a smooth fade, not a flash."""
        if name == self.settings["theme"]:
            return
        self.settings["theme"] = name
        save_settings(self.settings)
        C.update(THEMES[name])
        dim = [0.94, 0.86, 0.78]
        brighten = [0.84, 0.9, 0.95, 1.0]

        def step(index):
            if index < len(dim):
                self.root.attributes("-alpha", dim[index])
            elif index == len(dim):
                self.rebuild()
            else:
                self.root.attributes("-alpha", brighten[index - len(dim) - 1])
            if index < len(dim) + len(brighten):
                self.root.after(18, lambda: step(index + 1))

        step(0)

    def make_shortcut(self):
        self.shortcut_note.configure(text="Creating…", fg=C["muted"])

        def done(path, error):
            if error is not None:
                self.shortcut_note.configure(text=f"Couldn't create it: {error}", fg=C["red"])
            else:
                self.shortcut_note.configure(text=f"Done. It's on your desktop as {path.name}.", fg=C["green"])

        self.in_background(create_desktop_shortcut, done)

    def install_adb(self):
        if self.installing:
            return
        self.installing = True
        self.install_progress = 0.0
        self.render_setup()
        self.render_adb_note()

        def progress(fraction):
            self.events.put((lambda value, _: self.show_install_progress(value), fraction, None))

        self.in_background(lambda: download_platform_tools(progress), self.installed)

    def show_install_progress(self, fraction):
        self.install_progress = fraction
        text = f"Downloading adb from Google…  {fraction:.0%}"
        if self.install_label is not None and self.install_label.winfo_exists():
            self.install_label.configure(text=text)
        self.adb_note.configure(text=text)

    def installed(self, path, error):
        self.installing = False
        if error is not None:
            self.say(f"Couldn't install adb: {error}", "red")
        else:
            self.adb = path
            self.version = adb_version(path)
            self.say("adb is installed. Plug in your phone to start.", "green")
        self.setup_key = None
        self.render_setup()
        self.render_adb_note()
        self.polling = False
        self.poll(once=True)

    def render_adb_note(self):
        if self.installing:
            self.adb_note.configure(text=f"Downloading adb from Google…  {self.install_progress:.0%}", fg=C["muted"])
            self.update_button.configure_button(enabled=False)
            return
        if self.adb:
            own = Path(self.adb).parent == LOCAL_TOOLS
            # The home folder shortened to ~, so the path fits on one line instead of breaking mid-word.
            where = "screenadb's own copy" if own else self.adb.replace(str(Path.home()), "~")
            version = f"Version {self.version}\n" if self.version else ""
            self.adb_note.configure(text=f"{version}{where}", fg=C["muted"])
            self.update_button.configure_button(text="Download latest adb", enabled=True)
        else:
            self.adb_note.configure(text="Not installed yet. screenadb can fetch Google's official copy (about 8 MB).",
                                    fg=C["muted"])
            self.update_button.configure_button(text="Install adb", enabled=True)

    # ------------------------------------------------------------ Wi-Fi

    def refresh_wifi(self):
        if not hasattr(self, "wifi_button") or not self.wifi_button.winfo_exists():
            return
        device = self.current_device()
        usb = device is not None and device["state"] == "device" and not device["wifi"]
        self.wifi_button.configure_button(enabled=usb and not self.wifi_busy)
        self.pair_button.configure_button(enabled=self.adb is not None and not self.wifi_busy)

    def say_wifi(self, text, color="muted"):
        self.wifi_state = (text, color)
        if self.wifi_note.winfo_exists():
            self.wifi_note.configure(text=text, fg=C[color])

    def go_wifi(self):
        device = self.current_device()
        if self.wifi_busy or device is None or device["wifi"] or self.adb is None:
            return
        self.wifi_busy = True
        self.say_wifi("Switching to Wi-Fi…")
        self.refresh_wifi()
        adb, serial = self.adb, device["serial"]
        self.in_background(lambda: switch_to_wifi(adb, serial), self.went_wifi)

    def went_wifi(self, address, error):
        self.wifi_busy = False
        if error is not None:
            self.say_wifi(str(error), "red")
        else:
            self.remember_wifi(address)
            self.serial = address
            self.say_wifi(f"On Wi-Fi at {address}. You can unplug the cable.", "green")
        self.refresh_wifi()
        self.poll(once=True)

    def pair(self):
        address = self.pair_address.get().strip()
        code = self.pair_code.get().strip()
        if self.wifi_busy or self.adb is None:
            return
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", address) or not re.fullmatch(r"\d{6}", code):
            self.say_wifi("Enter the IP address & port (like 192.168.1.20:37099) and the 6-digit code.", "red")
            return
        self.wifi_busy = True
        self.say_wifi("Pairing…")
        self.refresh_wifi()
        adb = self.adb
        self.in_background(lambda: pair_wifi(adb, address, code), self.paired)

    def paired(self, address, error):
        self.wifi_busy = False
        if error is not None:
            self.say_wifi(f"Couldn't pair: {error}", "red")
        else:
            self.pair_code.delete(0, "end")
            if address:
                self.remember_wifi(address)
                self.serial = address
                self.say_wifi(f"Paired and connected at {address}.", "green")
            else:
                self.say_wifi("Paired. It connects by itself in a moment, while Wireless debugging is on.", "green")
            self.reconnect_at = 0.0
        self.refresh_wifi()
        self.poll(once=True)

    # ------------------------------------------------------------ background work

    def in_background(self, work, done):
        """Runs work off the window's thread and hands the result to done on it, so the window never freezes."""
        def job():
            try:
                self.events.put((done, work(), None))
            except Exception as exc:  # noqa: BLE001 - every failure is shown to the user
                self.events.put((done, None, exc))

        threading.Thread(target=job, daemon=True).start()

    def pump(self):
        while True:
            try:
                done, result, error = self.events.get_nowait()
            except queue.Empty:
                break
            done(result, error)
        self.root.after(PUMP_MS, self.pump)

    # ------------------------------------------------------------ adb and phone status

    def poll(self, once=False):
        if not self.polling and not self.installing:
            self.polling = True
            self.in_background(self.check, self.checked)
        if not once:
            self.root.after(POLL_MS, self.poll)

    def check(self):
        adb = self.adb if self.adb and Path(self.adb).exists() else find_adb()
        if adb is None:
            return None, "", []
        version = self.version if adb == self.adb and self.version else adb_version(adb)
        devices = list_devices(adb)
        if not any(device["state"] == "device" for device in devices) and time.time() >= self.reconnect_at:
            self.reconnect_at = time.time() + RECONNECT_SECONDS
            if self.reconnect(adb, devices):
                devices = list_devices(adb)
        return adb, version, devices

    def reconnect(self, adb, devices):
        """No phone ready: Wi-Fi phones used before, then any paired phone advertising on the network."""
        seen = {device["serial"] for device in devices if device["state"] == "device"}
        tried = set()
        for address in list(self.settings["wireless"]) + wifi_addresses(adb):
            if address in seen or address in tried:
                continue
            tried.add(address)
            if connect_wifi(adb, address):
                return True
        return False

    def remember_wifi(self, address):
        if not address or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", address):
            return
        # A phone gets a new port each time wireless debugging restarts, so its older addresses go.
        host = address.rsplit(":", 1)[0]
        kept = [a for a in self.settings["wireless"] if a.rsplit(":", 1)[0] != host]
        updated = [address, *kept][:MAX_REMEMBERED]
        if updated != self.settings["wireless"]:
            self.settings["wireless"] = updated
            save_settings(self.settings)

    def checked(self, result, error):
        self.polling = False
        if error is not None:
            self.adb_state = ("red", f"adb failed: {error}")
            self.set_status(self.adb_dot, self.adb_text, *self.adb_state)
            return
        self.adb, self.version, self.devices = result
        if self.adb is None:
            self.adb_state = ("red", "Not installed on this PC")
            self.phone_state = ("muted", "Waiting for adb")
            self.set_status(self.phone_dot, self.phone_text, *self.phone_state)
        else:
            self.adb_state = ("green", f"Ready  ·  {self.version}" if self.version else "Ready")
            for device in self.devices:
                if device["wifi"] and device["state"] == "device":
                    self.remember_wifi(device["serial"])
            self.show_phone()
        self.set_status(self.adb_dot, self.adb_text, *self.adb_state)
        self.render_setup()
        if not self.installing:
            self.render_adb_note()
        self.refresh_buttons()

    def show_phone(self):
        serials = [device["serial"] for device in self.devices]
        if self.serial not in serials:
            ready = [device for device in self.devices if device["state"] == "device"]
            self.serial = (ready or self.devices or [{"serial": None}])[0]["serial"]
        device = self.current_device()
        more = f"   ⇄ {len(self.devices)}" if len(self.devices) > 1 else ""
        if device is None:
            self.phone_state = ("amber", "No phone found")
        elif device["state"] == "device":
            link = f"Wi-Fi  ·  {device['serial']}" if device["wifi"] else f"USB  ·  {device['serial']}"
            self.phone_state = ("green", f"{device['model']}  ·  {link}{more}")
        elif device["state"] == "unauthorized":
            self.phone_state = ("amber", f"Waiting for you to allow it{more}")
        else:
            self.phone_state = ("red", f"{device['serial']} is {device['state']}{more}")
        self.set_status(self.phone_dot, self.phone_text, *self.phone_state)
        self.refresh_wifi()

    def render_setup(self):
        """Shows what's needed to get going, if anything: installing adb, turning on USB debugging, allowing it."""
        device = self.current_device()
        if self.installing:
            key = ("installing",)
        elif self.adb is None:
            key = ("missing",) if self.adb_state[0] == "red" else None
        elif device is None:
            key = ("no-phone",)
        elif device["state"] == "unauthorized":
            key = ("unauthorized",)
        else:
            key = None
        if key == self.setup_key:
            return
        self.setup_key = key
        for child in self.setup.winfo_children():
            child.destroy()
        self.install_label = None
        if key is None:
            self.setup.pack_forget()
            self.preview.pack(pady=(self.px(20), 0), before=self.message)
            self.fit()
            return
        # No shot can be taken until setup is done, so the empty preview steps aside to keep the window short.
        if self.last_thumb is None:
            self.preview.pack_forget()

        def text(content, color="muted", bold=False):
            label = tk.Label(self.setup, text=content, bg=C["bg"], fg=C[color], justify="left", anchor="w",
                             font=("Segoe UI Semibold" if bold else "Segoe UI", 9), wraplength=self.px(300))
            label.pack(fill="x", pady=(self.px(2), 0))
            return label

        if key == ("installing",):
            text("Setting up adb", "text", bold=True)
            self.install_label = text(f"Downloading adb from Google…  {self.install_progress:.0%}")
        elif key == ("missing",):
            text("adb isn't on this PC yet", "text", bold=True)
            text("screenadb needs adb to talk to your phone. It can fetch Google's official platform-tools "
                 "(about 8 MB) and keep it in its own folder.")
            PillButton(self.setup, "Install adb", self.install_adb, True, "", self.px(42), self.px).pack(
                fill="x", pady=(self.px(8), 0))
        elif key == ("no-phone",):
            text("Connect your phone", "text", bold=True)
            text("1.  On the phone: Settings › About phone › tap Build number 7 times.\n"
                 "2.  Settings › Developer options › turn on USB debugging.\n"
                 "3.  Plug the phone in with a USB cable that carries data.")
            text("Or wirelessly, on the same Wi-Fi: turn on Developer options › Wireless debugging and pair it "
                 "once in Settings › Wireless. After that it connects by itself.")
        else:
            text("Allow this PC on your phone", "text", bold=True)
            text("Unlock the phone and tap Allow on the USB debugging prompt. Tick 'Always allow' to skip this "
                 "next time.")
        self.setup.pack(fill="x", pady=(self.px(8), 0), before=self.copy_button)
        self.fit()

    def current_device(self):
        return next((device for device in self.devices if device["serial"] == self.serial), None)

    def next_device(self):
        if len(self.devices) < 2:
            return
        serials = [device["serial"] for device in self.devices]
        self.serial = serials[(serials.index(self.serial) + 1) % len(serials)]
        self.show_phone()
        self.refresh_buttons()

    def set_status(self, dot, label, color, text):
        dot.configure(fg=C[color])
        label.configure(text=text)

    def ready(self):
        device = self.current_device()
        return self.adb is not None and device is not None and device["state"] == "device"

    def refresh_buttons(self):
        enabled = self.ready() and not self.busy
        self.copy_button.configure_button(enabled=enabled)
        self.save_button.configure_button(enabled=enabled)

    # ------------------------------------------------------------ screenshots

    def key_take(self, copy):
        # A key press gets the same press-and-spring as a click.
        if self.view == "main" and self.take(copy):
            (self.copy_button if copy else self.save_button).pulse()

    # A click uses a background shot if one is fresh, waits for one already on its way, or else asks the phone.
    def take(self, copy):
        if self.busy or not self.ready():
            return False
        self.busy = True
        clicked = time.perf_counter()
        button = self.copy_button if copy else self.save_button
        button.configure_button(text="Capturing…")
        self.refresh_buttons()
        self.say("", "muted")
        fresh = self.latest
        if fresh and fresh[2] == self.serial and time.monotonic() - fresh[1] < FRESH_SECONDS:
            self.finish(fresh[0], copy, clicked)
        elif self.fetching:
            self.waiting = (copy, clicked)
        else:
            adb, serial = self.adb, self.serial
            self.in_background(
                lambda: capture(adb, serial),
                lambda image, error: self.fail(error) if error else self.finish(image, copy, clicked),
            )
        return True

    def finish(self, image, copy, clicked):
        def work():
            # Bitmap conversion and writing the file stay off the window's thread.
            return clipboard_formats(image) if copy else None, None if copy else self.write(image)

        def done(result, error):
            if error is not None:
                self.fail(error)
                return
            formats, path = result
            if copy:
                try:
                    set_clipboard(formats)
                except RuntimeError as exc:
                    self.fail(exc)
                    return
            seconds = time.perf_counter() - clicked
            self.idle()
            if copy:
                self.say(f"Copied {image.width}×{image.height}  ·  {seconds:.2f}s  ·  paste with Ctrl+V", "green")
            else:
                self.say(f"Saved {path.name}  ·  {seconds:.2f}s", "green")
            self.last_image = image
            self.last_path = path
            # The preview comes after: the shot is already on the clipboard or in its file.
            self.in_background(lambda: self.thumbnail(image), lambda thumb, _: thumb and self.show_preview(thumb))
            self.prefetch()

        self.in_background(work, done)

    def thumbnail(self, image):
        thumb = image.copy()
        thumb.thumbnail(self.preview_box(), Image.LANCZOS, reducing_gap=2.0)
        return thumb

    def fail(self, error):
        self.idle()
        self.say(f"Couldn't capture: {error}", "red")

    def idle(self):
        self.busy = False
        self.copy_button.configure_button(text="Copy screenshot")
        self.save_button.configure_button(text="Take screenshot")
        self.refresh_buttons()

    # Checked on a timer rather than with enter/leave events, which fire for every widget the pointer crosses.
    def watch_pointer(self):
        x, y = self.root.winfo_pointerxy()
        left, top = self.root.winfo_rootx(), self.root.winfo_rooty()
        inside = left <= x < left + self.root.winfo_width() and top <= y < top + self.root.winfo_height()
        if inside != self.hovering:
            self.hovering = inside
            self.prefetch()
        self.root.after(POINTER_MS, self.watch_pointer)

    def prefetch(self):
        """While the pointer is over the window, shots keep coming one after another in the background, so a click
        finds one the phone took within the last second instead of waiting for it."""
        if not self.hovering or self.fetching or self.busy or not self.ready():
            return
        self.fetching = True
        adb, serial, started = self.adb, self.serial, time.monotonic()

        def done(image, error):
            self.fetching = False
            if error is None:
                self.latest = (image, started, serial)
            waiting, self.waiting = self.waiting, None
            if waiting is not None:
                copy, clicked = waiting
                if error is None:
                    self.finish(image, copy, clicked)
                else:
                    self.fail(error)
                return
            self.prefetch()

        self.in_background(lambda: capture(adb, serial), done)

    def write(self, image):
        SHOTS.mkdir(exist_ok=True)
        path = SHOTS / f"screen_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        counter = 2
        while path.exists():
            path = SHOTS / f"screen_{datetime.now():%Y-%m-%d_%H-%M-%S}_{counter}.png"
            counter += 1
        # Light compression: the file is a little bigger, but it's written several times faster.
        image.save(path, "PNG", compress_level=1)
        return path

    def say(self, text, color):
        self.message_state = (text, color)
        self.message.configure(text=text, fg=C[color])

    # ------------------------------------------------------------ preview

    def preview_box(self):
        # A phone's shape: about 3 wide for 5 tall.
        return round(self.preview_height * 0.6), self.preview_height

    def redraw_preview(self):
        """Draws the preview again at its current size, after the window was fitted to a different screen."""
        if self.last_image is not None:
            self.show_preview(self.thumbnail(self.last_image), flash=False)
        else:
            self.show_placeholder()

    def show_placeholder(self):
        w, h = self.preview_box()
        art = rounded((w, h), self.px(16), C["surface"], outline=C["border"], width=max(1, self.px(1)))
        draw = ImageDraw.Draw(art)
        # A little phone outline, so the empty slot says what will appear there; it scales with the slot.
        ph = round(h * 0.23)
        pw = round(ph * 0.58)
        cx, cy = w // 2, h // 2 - round(h * 0.03)
        draw.rounded_rectangle((cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2), radius=max(3, ph // 7),
                               outline=C["phone"], width=max(2, round(ph / 19)))
        self.preview_image = ImageTk.PhotoImage(art)
        self.preview.configure(image=self.preview_image, text="", cursor="arrow")

    def show_preview(self, thumb, flash=True):
        self.last_thumb = thumb
        shaped = round_corners(thumb, self.px(14))
        self.preview.configure(cursor="hand2")
        if not flash:
            self.preview_image = ImageTk.PhotoImage(shaped)
            self.preview.configure(image=self.preview_image)
            return
        # A camera-flash: the new shot starts washed white and clears over a few frames.
        white = Image.new("RGBA", shaped.size, (255, 255, 255, 255))
        self.flash_frames = []
        for strength in (0.85, 0.6, 0.38, 0.2, 0.08, 0.0):
            frame = Image.blend(shaped, white, strength)
            frame.putalpha(shaped.getchannel("A"))
            self.flash_frames.append(ImageTk.PhotoImage(frame))
        self.play_flash(0)

    def play_flash(self, index):
        if index >= len(self.flash_frames) or not self.preview.winfo_exists():
            return
        self.preview_image = self.flash_frames[index]
        self.preview.configure(image=self.preview_image)
        self.root.after(32, lambda: self.play_flash(index + 1))

    def open_last(self):
        if self.last_image is None:
            return
        path = self.last_path
        if path is None:
            # A copied shot has no file yet, so a temporary one is opened instead.
            path = Path(tempfile.gettempdir()) / "screenadb_last.png"
            self.last_image.save(path, "PNG", compress_level=1)
        os.startfile(path)

    def open_folder(self):
        SHOTS.mkdir(exist_ok=True)
        os.startfile(SHOTS)

    # ------------------------------------------------------------ window

    def restore_position(self):
        """Opens where it was left, unless that spot is now off every screen (say, a monitor was unplugged)."""
        position = self.settings["position"]
        if position is None:
            return
        metrics = ctypes.windll.user32.GetSystemMetrics
        left, top = metrics(76), metrics(77)
        right, bottom = left + metrics(78), top + metrics(79)
        x, y = position
        if left <= x < right - self.px(80) and top <= y < bottom - self.px(80):
            self.root.geometry(f"+{x}+{y}")

    def close(self):
        self.settings["position"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_settings(self.settings)
        self.root.destroy()

    def toggle_pin(self):
        self.settings["pinned"] = not self.settings["pinned"]
        save_settings(self.settings)
        self.root.attributes("-topmost", self.settings["pinned"])
        self.show_pin()

    def show_pin(self):
        pinned = self.settings["pinned"]
        self.pin_button.set_active(pinned, "Kept on top  ·  click to stop  (P)" if pinned else "Keep on top  (P)")


def dark_title_bar(root, dark):
    """Windows 11 draws the title bar to match the theme; older Windows just ignores the request."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def main():
    # Sharp text on high-DPI screens, and the taskbar shows screenadb's own icon rather than Python's.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("someless.screenadb")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    app = ScreenAdb(root)
    root.update_idletasks()
    dark_title_bar(root, app.settings["theme"] == "dark")
    root.mainloop()


if __name__ == "__main__":
    main()
