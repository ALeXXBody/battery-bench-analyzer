# Repair & Unlock Hooks

BatteryClinic ships a **documented extension interface** for SMBus fuel-gauge
interop, but by design ships **no working proprietary reflash** and no "chip
unlock" routine.

## Why the boundary

Tools such as NLBA1 and similar "battery repair/unlock" software typically:

1. Read the fuel gauge over SMBus.
2. Write to the gauge's EEPROM to **zero the cycle count** or clear a
   **permanent failure / dead-battery** flag.

These writes are the actual "unlock". We chose not to ship a turnkey one:

- **Safety:** forcing a physically failed pack back to life can cause a fire.
- **Licensing:** those tools are closed-source (some AGPL). We are a clean-room
  reimplementation and share no code with them.
- **Legal:** wiping a cycle count to make a worn pack look "new" and reselling
  it is counterfeiting in most jurisdictions.

So BatteryClinic gives you the *bus* and *guarded writes*, but the register map
and protocol for your specific gauge are up to you.

## What the hooks give you

- `SMbusBus` — abstract I²C/SMBus access (implement with FTDI/CH341/EV2300).
- `Cp2112Smbus` — a ready-made driver for the **CP2112 USB-to-SMBus adapter**
  (Silicon Labs). It talks to the adapter over the Windows **HID** interface, so
  no vendor kernel driver is needed, and it natively handles the SMBus clock
  stretching that battery fuel gauges require. See `battery_clinic/unlock/cp2112.py`.
- `FuelGaugeInterop` — high-level read-mostly access to the standard Smart
  Battery register set (public spec), e.g. `probe()` returns manufacturer,
  device, chemistry, serial, capacities, cycle count.
- Guarded `write_word()` that requires the user to explicitly acknowledge a
  safety/legal statement first (`grant_write_permission`).

## Using the CP2112 adapter

Plug the CP2112 into the battery cradle (gauge typically at SMBus address `0x0B`),
then either:

- **GUI**: open the *Repair / Unlock* page → *Detect CP2112 adapter* → *Probe
  Gauge* (read-only).
- **CLI**:
  ```bash
  python -m battery_clinic.cli smbus --adapter cp2112
  ```

The driver reimplements the host protocol from Silicon Labs Application Note
AN495 (public); no Silicon Labs source code is used. It defaults to 100 kHz and
configures auto-send-read so a word/block read is one request/response pair:

- word read: `read_word(addr, reg)` returns the uint16 at `reg`.
- word write: `write_word(addr, reg, value)` writes the command byte plus the
  SMBus little-endian word data — **gated** behind the consent flow.
- block read: `read_block(addr, reg, n)` returns the raw Smart-Battery
  "Bytes"-format payload (leading length byte preserved) so the higher-level
  readers work unchanged.

> CP2112 needs Windows (its HID stack). On Linux the adapter is reachable via
> the kernel `hidraw` interface instead; build the server/CLI on a Windows host
> to drive it directly.

## Wiring a real bus

For a laptop battery *in the machine*, the gauge is usually on the internal
I²C bus and not externally addressable without board-level work. An "out of
machine" setup needs a battery cradle + an SMBus adapter (e.g. CH341A),
plus your own register map. The `SMbusBus` abstract class is where that adapter
driver goes:

```python
from battery_clinic.unlock.smbus import SMbusBus, FuelGaugeInterop

class Ch341Bus(SMbusBus):
    def read_word(self, addr, reg): ...   # adapter-specific
    def write_word(self, addr, reg, value): ...
    def read_block(self, addr, reg, length): ...

g = FuelGaugeInterop(Ch341Bus())
info = g.probe()
print(info)          # read-only analysis of the gauge
```

## Baseline to add

Before building any vendor unlock, add tests in `tests/test_unlock.py` using the
provided `FakeSMbusBus` so the guarded-write consent logic is verified.

## Real-world caution

If `probe()` shows a genuinely failed cell (voltage collapse, zero capacity,
internal short), **do not** attempt to revive it. Recycle the pack.
