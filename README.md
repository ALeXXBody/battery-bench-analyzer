# Battery Bench Analyzer

A modern Windows desktop application for **bench testing, repairing and
calibrating laptop battery packs** — the pack comes out of the device and onto
the bench, its fuel gauge wired to a CP2112 USB-SMBus adapter.

Battery Bench Analyzer (a.k.a. BatteryBenchAnalyzer, BBA) is a **clean-room
reinvention** of the general ideas found in bench battery analyzers. It shares
**no code** with those projects and is built from scratch. It only uses:

- The public **Smart Battery Data v1.1** register specification.
- Silicon Labs' public **AN495** CP2112 interface specification.
- Safe, read-only interface operations by default.

Battery Bench Analyzer is **not affiliated with, endorsed by, or derived from** any
vendor's battery tooling.

## What it can do

- **Bench Analyzer** — Connect to the out-of-circuit pack's gauge over a
  CP2112 USB-SMBus adapter (native SMBus clock stretching, Windows HID — no
  vendor driver): voltage, signed current, temperature, state of charge,
  design/full/remaining capacity, cycle count, serial, manufacture date,
  chemistry and status flags — with live 1 s telemetry.
- **Regenerate / Calibrate** — Guided learn cycle (charge → discharge →
  recharge): you switch the bench charger/load when prompted, the app monitors
  the gauge and records the learned FullChargeCapacity/SoH before and after.
- **Repair / Unlock (hooks)** — Read-only probe plus an opt-in, consent-gated
  write path for users who know their gauge's register map. No vendor unlock
  or EEPROM routines are shipped. See [`docs/UNLOCK.md`](docs/UNLOCK.md).
- **Auto-update** — Silent GitHub Releases check on startup plus a sidebar
  **Check for updates** button; offers to download and run the new installer.
- **Demo gauge** — A realistic in-memory 3S pack lets you try the entire
  workflow with no hardware.

Bench wiring and workflow: **[docs/BENCH.md](docs/BENCH.md)**.

## Important safety & legality note

- **Never open a Li-ion pack.** Lithium polymer/cobalt packs are a fire hazard
  if punctured or shorted. BatteryBenchAnalyzer does **not** require opening the pack.
- **Do not rely on software to revive physically failed cells.** A "repair"
  that causes a battery to hold a charge after cell damage is a fire risk.
  Use good judgement; if in doubt, recycle the pack.
- Proprietary fuel-gauge EEPROM reflashing to "reset" a worn battery is, in
  most jurisdictions, **counterfeit / tampering** and is not shipped here.

## Requirements

- Windows 10/11 (Python 3.10+)
- `pip install -r requirements.txt`

## Install

The easiest way is the **installer**: grab `BatteryBenchAnalyzer-<version>-setup.exe`
from the [Releases](../../releases) page. The installer installs BatteryBenchAnalyzer with
a Start Menu shortcut, optional desktop icon, and an uninstaller.

A portable `BatteryBenchAnalyzer.exe` (onefile, no Python needed) is also published on
every release for those who prefer not to install.

From source:

```bat
pip install -r requirements.txt
python -m benchanalyzer
```

A modern dark UI opens. The left panel walks you through:
**Analyze → Regenerate → (Optional) Unlock**. The sidebar also shows the app
version and a **Check for updates** button.

## CLI

```bat
:: bench battery via CP2112 (demo adapter needs no hardware)
benchanalyzer analyze --adapter cp2112 --watch 30
benchanalyzer analyze --adapter demo

:: guided learn cycle (you switch the bench power when prompted)
benchanalyzer calibrate --adapter cp2112 --low 10

:: legacy: this machine's own battery
benchanalyzer analyze --internal
benchanalyzer regen --cycles 3
```

## Project layout

```
benchanalyzer/
  core/       Models, bench session + guided calibration, SBS register layer,
              Windows IOCTL internal reader (legacy)
  regen/      Internal-battery calibration engine (legacy)
  unlock/     Consent-gated gauge writes + CP2112 USB-SMBus adapter driver
  gui/        PySide6 modern GUI (pages, widgets, charts)
  cli/        Headless CLI for scripting and automation
  updater.py  GitHub-Releases auto-update check/download
assets/       App icon (SVG source + generated .ico/.png)
installer/    Inno Setup script that produces the Windows installer
```


## Install by platform

| Platform | File | How to install |
| :------- | :--- | :------------- |
| **Windows** | `BatteryBenchAnalyzer-x.y.z-setup.exe` | Run the installer (Inno Setup). The app auto-updates from GitHub Releases. |
| **Linux** | `BatteryBenchAnalyzer-x.y.z-linux-x86_64.tar.gz` | `tar -xzf BatteryBenchAnalyzer-*.tar.gz` and run `./BatteryBenchAnalyzer/BatteryBenchAnalyzer`. |
| **Linux (Debian/Ubuntu)** | `BatteryBenchAnalyzer-x.y.z-linux-amd64.deb` | `sudo apt install ./BatteryBenchAnalyzer-*.deb` — adds a `battery-bench-analyzer` command and menu entry. |
| **macOS (Apple Silicon)** | `BatteryBenchAnalyzer-x.y.z-macos.dmg` | Open the DMG and drag the app to Applications. The app is **ad-hoc signed, not notarized**: on first launch use *right-click → Open* (or `xattr -cr /Applications/BatteryBenchAnalyzer.app`) to pass Gatekeeper. |

### Linux udev permission

Raw HID access to the CP2112 / MCP2221A needs read/write on `/dev/hidraw*`.
That already works if you are in the `plugdev` group; otherwise install a
udev rule (as root):

    # /etc/udev/rules.d/99-batterybench.rules
    SUBSYSTEM=="hidraw", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea90", MODE="0660", GROUP="plugdev"
    SUBSYSTEM=="hidraw", ATTRS{idVendor}=="04d8", ATTRS{idProduct}=="00dd", MODE="0660", GROUP="plugdev"

then `sudo udevadm control --reload-rules`, replug the adapter, and
(optionally) unbind a conflicting kernel driver if the adapter refuses
raw access:

    echo 10C4:EA90 | sudo tee /sys/bus/hid/drivers/hid-generic ... # see docs below

If your distro auto-attaches the `hid-cp2112` kernel driver and SMBus reads
misbehave from hidraw, `sudo modprobe -r cp2112` (or blacklist it) frees the
device for userspace access.

### macOS notes

- Tested on Apple Silicon (M1/M2). The build bundles PySide6, so no extra
  runtime is needed.
- Auto-update is Windows-only for now; on macOS/Linux update by downloading
  a newer release asset manually.

## License

MIT — see [LICENSE](LICENSE). This project is original work and intentionally
contains **no vendor AGPL/GPL code** and no copied protocol implementations.