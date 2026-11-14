<p align="center">
  <img src="screenadb.png" width="96" alt="screenadb icon">
</p>

<h1 align="center">screenadb</h1>

<p align="center">
  <b>Your Android phone's screen, on your PC's clipboard, in one key press.</b><br>
  A tiny Windows app that grabs phone screenshots over adb, by USB cable or over Wi-Fi.
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white">
  <img alt="Android" src="https://img.shields.io/badge/Android-USB%20%26%20Wi--Fi-3DDC84?logo=android&logoColor=white">
  <img alt="Version" src="https://img.shields.io/badge/version-1.2-FF444F">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
</p>

<p align="center">
  <img src="docs/hero.png" alt="screenadb in dark and light themes, and its Settings page" width="100%">
</p>

---

## Why

Building an Android app, writing docs or reporting a bug, you want what's on the phone in your chat, issue or design
tool **now**. Not after emailing it to yourself, not after hunting for it in a gallery.

Press **C** and the phone's screen is on your clipboard, ready to paste. Press **S** and it's saved as a PNG.

## Features

- **Copy or save in one key.** `C` copies to the clipboard, `S` saves a PNG into the `shots` folder.
- **Near-instant shots.** While your pointer is over the window, screenadb keeps a fresh shot ready in the
  background, so a click lands at once. Otherwise a shot takes about half a second.
- **USB or Wi-Fi.** Use a cable, switch a plugged-in phone to Wi-Fi with one click, or pair with a code on Android 11+.
- **Reconnects by itself.** A phone you've used over Wi-Fi comes back on its own whenever no phone is ready, even
  after its wireless debugging port changes.
- **Several phones.** Click the phone line to switch between connected phones.
- **No adb? No problem.** screenadb offers to download Google's official platform-tools (about 8 MB) into its own
  folder.
- **Dark and light themes**, sharp on high-DPI screens, and a **keep on top** pin for working alongside your editor.
- **One file**, plain Python. Pillow is the only dependency.

## Screenshots

| Dark | Light |
| :---: | :---: |
| <img src="docs/main-dark.png" width="360" alt="screenadb, dark theme"> | <img src="docs/main-light.png" width="360" alt="screenadb, light theme"> |
| <img src="docs/settings-dark.png" width="360" alt="Settings, dark theme"> | <img src="docs/settings-light.png" width="360" alt="Settings, light theme"> |

## Install

You need **Windows 10 or 11** and **Python 3.8+**.

```powershell
git clone https://github.com/boyoftime/screenadb.git
cd screenadb
py -m pip install -r requirements.txt
pyw screenadb.pyw
```

Then open **Settings › Create desktop shortcut** to launch it from the desktop with its own icon.

> If adb isn't installed, screenadb says so and offers **Install adb**. It uses an adb already on your `PATH` or in
> the Android SDK if it finds one.

## Connect your phone

### With a USB cable

1. On the phone: **Settings › About phone**, then tap **Build number** 7 times to turn on Developer options.
2. **Settings › Developer options**, then turn on **USB debugging**.
3. Plug the phone in with a cable that carries data, then tap **Allow** on the phone. Tick *Always allow* to skip
   this next time.

### Over Wi-Fi

The phone and the PC need to be on the same Wi-Fi.

- **From USB, one click:** with the phone plugged in, open **Settings › Wireless › Switch this phone to Wi-Fi**, then
  unplug it. After the phone restarts, plug it in once more and switch it again.
- **No cable, Android 11+:** on the phone, open **Developer options › Wireless debugging › Pair device with pairing
  code**. In screenadb, open **Settings › Wireless**, enter the *IP address & port* and the *pairing code* it shows,
  then press **Pair**. You only pair once. From then on the phone connects by itself whenever Wireless debugging is
  on.

## Keys

| Key | What it does |
| :-: | --- |
| `C` | Copy a screenshot to the clipboard |
| `S` | Save a screenshot into `shots/` |
| `O` | Open the `shots` folder |
| `P` | Keep the window on top (or stop) |
| `Esc` | Leave Settings |

Clicking the preview opens the last shot.

## How it's fast

A screenshot normally travels as a PNG that the phone has to compress, which is slow. screenadb asks the phone for
raw pixels squeezed with `gzip -1`, and reads them straight from the adb server's socket instead of starting a new
`adb.exe` for every shot. On a Galaxy S22 that's about **0.3 s per shot**, against 0.45 s for PNG and 1 s for
unpacked pixels. Phones without `gzip` fall back to PNG automatically.

## Files

| File | What it is |
| --- | --- |
| `screenadb.pyw` | The whole app |
| `screenadb.png`, `screenadb.ico` | The icon for the window, shortcut and taskbar |
| `make_icon.py` | Draws the icon; run it again after changing the design |
| `docs/` | The screenshots in this README |

On first run, screenadb also creates `settings.json` (theme, pin, window position, Wi-Fi phones), `shots/` and,
if you let it install adb, `platform-tools/`. They're kept out of git by `.gitignore`.

## Troubleshooting

- **"No phone found."** Check that USB debugging is on and the cable carries data. Some cables only charge. Over
  Wi-Fi, check that Wireless debugging is on and both devices share the network.
- **"Waiting for you to allow it."** Unlock the phone and tap **Allow** on the USB debugging prompt.
- **Pairing fails.** The code changes every time the pairing dialog opens. Use the address and code that are on
  screen right now.
- **A switched-to-Wi-Fi phone stopped connecting.** Restarting the phone turns that mode off. Plug it in and press
  **Switch this phone to Wi-Fi** again, or pair it with a code, which survives restarts.

## License

[MIT](LICENSE): free to use, change and share, in personal or commercial projects.

---

<p align="center">
  <img src="someless_tricks.png" width="220" alt="Someless Tricks"><br>
  Made by <b>Someless Tricks</b>
</p>
