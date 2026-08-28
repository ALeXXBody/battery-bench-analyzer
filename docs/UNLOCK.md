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
- `FuelGaugeInterop` — high-level read-mostly access to the standard Smart
  Battery register set (public spec), e.g. `probe()` returns manufacturer,
  device, chemistry, serial, capacities, cycle count.
- Guarded `write_word()` that requires the user to explicitly acknowledge a
  safety/legal statement first (`grant_write_permission`).

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
