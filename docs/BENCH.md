# Bench Setup Guide

VoltClinic is built for **out-of-circuit battery work**: the pack comes out of
the device and onto the bench, where its fuel gauge is wired to a USB-SMBus
adapter. The app talks to the gauge directly — it does **not** use (or need)
the host machine's own battery.

## Wiring

Laptop packs expose their Smart Battery gauge on the pack connector. The three
wires you need:

| Pack pin        | CP2112 board | Notes                                   |
|-----------------|--------------|-----------------------------------------|
| SMBus clock     | SCL          | sometimes labeled `SMBC` / `C`          |
| SMBus data      | SDA          | sometimes labeled `SMBD` / `D`          |
| Ground          | GND          |                                         |

- The gauge is an SMBus *slave* at address **0x0B** (the SBS standard). Use the
  **Scan addresses** button if in doubt.
- The adapter's 3.3 V pull-ups are usually enough. Do **not** feed the pack's
  pack-plus (P+/P-) terminals into the adapter — that is the main current path.
- The thermistor / battery-ID pins can stay unconnected for read-only work.
- Never short a pack connector. Insulated probes only.

## What the app reads

Everything comes from the public **Smart Battery Data v1.1** register set:
voltage, current (signed: + charge / − discharge), temperature, relative state
of charge, remaining / full-charge / design capacity, design voltage, cycle
count, serial, manufacture date, manufacturer / device name / chemistry, and
the BatteryStatus alarm flags.

## Analyze

1. Bench Analyzer page → adapter **CP2112** (or **Demo gauge** without
   hardware) → **Connect**.
2. Identity block + live telemetry (1 s polling) appear.
3. CLI equivalent: `voltclinic analyze --adapter cp2112 --watch 30`.

## Calibrate (learn cycle)

A gauge re-learns true capacity from a full learn cycle. VoltClinic *guides*
the cycle — **you** switch the bench power, the app watches the gauge:

1. **Charge** to full (gauge raises FULLY_CHARGED),
2. **Discharge** to the low threshold (default 10% RSOC),
3. **Recharge** to full; the gauge then reports its learned
   FullChargeCapacity.

The app records FCC/SoH before and after. CLI: `voltclinic calibrate --adapter
cp2112 --low 10`.

Safety: the pack's firmware and your bench supply remain the actual limits.
The app never drives any hardware. Never leave a pack unattended; recycle
rather than revive physically failed cells.

## Repair / Unlock

Read-only probe plus an **opt-in**, consent-gated write path
(`ManufacturerAccess` and word writes) for people who know their gauge's
register map. VoltClinic ships no vendor unlock/EEPROM routines on purpose —
see [UNLOCK.md](UNLOCK.md).
