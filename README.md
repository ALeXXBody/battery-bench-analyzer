# VoltClinic

A modern Windows desktop application for analyzing, repairing, unlocking and
regenerating laptop batteries.

VoltClinic is a **clean-room reinvention** of the general ideas found in
tools like NLBA1 / "battery repair" utilities. It shares **no code** with those
projects and is built from scratch. It only uses:

- Public documentation of the ACPI/SMBIOS battery interfaces exposed by the
  operating system.
- The generic SMBus/I²C protocols that the SMBus spec makes public.
- Safe, read-only interface operations.

VoltClinic is **not affiliated with, endorsed by, or derived from** any
vendor's battery tooling.

## What it can do

- **Analyze** — Read your battery's design/full capacity, charge level,
  charge/discharge rates, voltage, chemistry, serial, temperature, cycle count
  and health (State of Health %) straight from Windows' documented battery
  class driver interface (SetupAPI + IOCTL, pure ctypes — no pywin32).
- **Regenerate / Recalibrate** — Safe, supervised charge/discharge cycling that
  re-learns full-charge capacity and improves reported health. Runs under a
  power-policy guard so the laptop never sleeps mid-cycle.
- **Repair / Unlock (hooks)** — A documented extension architecture for optional
  SMBus-fuel-gauge interop, including a ready-made **CP2112 USB-to-SMBus** adapter
  driver (Windows HID, no vendor driver needed). Solder-and-adapter work on the
  battery pack itself is out of scope for safety and licensing reasons; the hooks
  let skilled users plug in their own protocol modules.
  See [`docs/UNLOCK.md`](docs/UNLOCK.md).
- **Auto-update** — On startup (and via the sidebar button) VoltClinic checks
  GitHub Releases for a newer version. When one exists it offers to download and
  run the new installer. Runs in the background; never blocks startup.

## Important safety & legality note

- **Never open a Li-ion pack.** Lithium polymer/cobalt packs are a fire hazard
  if punctured or shorted. VoltClinic does **not** require opening the pack.
- **Do not rely on software to revive physically failed cells.** A "repair"
  that causes a battery to hold a charge after cell damage is a fire risk.
  Use good judgement; if in doubt, recycle the pack.
- Proprietary fuel-gauge EEPROM reflashing to "reset" a worn battery is, in
  most jurisdictions, **counterfeit / tampering** and is not shipped here.

## Requirements

- Windows 10/11 (Python 3.10+)
- `pip install -r requirements.txt`

## Install

The easiest way is the **installer**: grab `VoltClinic-<version>-setup.exe`
from the [Releases](../../releases) page. The installer installs VoltClinic with
a Start Menu shortcut, optional desktop icon, and an uninstaller.

A portable `VoltClinic.exe` (onefile, no Python needed) is also published on
every release for those who prefer not to install.

From source:

```bat
pip install -r requirements.txt
python -m voltclinic
```

A modern dark UI opens. The left panel walks you through:
**Analyze → Regenerate → (Optional) Unlock**. The sidebar also shows the app
version and a **Check for updates** button.

## Project layout

```
voltclinic/
  core/       Battery data model, Windows IOCTL read layer, SoH calculations
  regen/      Calibration & regeneration engine, power-policy guard
  unlock/     SMBus extension hooks + CP2112 USB-to-SMBus adapter driver
  gui/        PySide6 modern GUI (pages, widgets, charts)
  cli/        Headless CLI for scripting and automation
  updater.py  GitHub-Releases auto-update check/download
assets/       App icon (SVG source + generated .ico/.png)
installer/    Inno Setup script that produces the Windows installer
```

## License

MIT — see [LICENSE](LICENSE). This project is original work and intentionally
contains **no vendor AGPL/GPL code** and no copied protocol implementations.