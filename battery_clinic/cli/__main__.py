"""Headless CLI for analysis, regeneration and (optional) SMBus probing."""
from __future__ import annotations

import argparse
import sys

from ..core.models import BatteryInfo
from ..core.reader import BatteryReader
from ..core.regeneration import RegenerationEngine
from ..unlock.smbus import (
    FakeSMbusBus,
    FuelGaugeInterop,
    SB_CYCLE_COUNT,
    SB_DESIGN_CAPACITY,
    SB_DEVICE_NAME,
    SB_FULL_CHARGE_CAPACITY,
    SB_MANUFACTURER_NAME,
    SB_SERIAL_NUMBER,
    SMbusBus,
)


def _print_battery(info: BatteryInfo) -> None:
    for line in info.summary_lines():
        print(line)


def cmd_analyze(args) -> int:
    reader = BatteryReader()
    batteries = reader.refresh()
    if not batteries:
        print("No batteries found.")
        print(f"Backend: {reader.backend_name} (only usable on Windows with a battery).")
        return 1
    for i, b in enumerate(batteries):
        print(f"=== Battery {i + 1} ===")
        _print_battery(b)
        print()
    return 0


def cmd_regen(args) -> int:
    reader = BatteryReader()
    batteries = reader.refresh()
    if not batteries:
        print("No batteries found to regenerate.")
        return 1
    engine = RegenerationEngine(reader, battery_index=args.index,
                                max_cycles=args.cycles)
    print(f"Starting regeneration (max {args.cycles} cycles). "
          f"Keep an eye on the machine. Ctrl+C to stop safely at endpoints.")
    result = engine.run(progress=lambda msg, frac: print(f"  [{frac*100:5.1f}%] {msg}"))
    print("\nRegeneration finished.")
    print(f"  Cycles: {result.cycles_completed}")
    b = result.before_soh
    a = result.after_soh
    print(f"  SoH before: {f'{b:.1f}%' if b is not None else 'unknown'}")
    print(f"  SoH after:  {f'{a:.1f}%' if a is not None else 'unknown'}")
    return 0


def cmd_smbus(args) -> int:
    bus: SMbusBus
    if args.demo:
        bus = FakeSMbusBus(regs={
            SB_SERIAL_NUMBER: 0x1234,
            SB_DESIGN_CAPACITY: 4200,
            SB_FULL_CHARGE_CAPACITY: 3100,
            SB_CYCLE_COUNT: 402,
        })
        bus.blocks[SB_MANUFACTURER_NAME] = b"\x07" + b"TEST-CO"
        bus.blocks[SB_DEVICE_NAME] = b"\x08" + b"DEMO-PAK"
        print("[demo mode: using in-memory fake gauge]")
    else:
        print("No real SMBus adapter driver is bundled. Use --demo to see the "
              "read path, or implement SMbusBus for your adapter.")
        return 1

    gauge = FuelGaugeInterop(bus)
    info = gauge.probe()
    print(f"  Address:        0x{info.address:02X}")
    print(f"  Manufacturer:   {info.manufacturer}")
    print(f"  Device:         {info.device_name}")
    print(f"  Chemistry:      {info.chemistry}")
    print(f"  Serial (hex):   0x{info.serial:04X}" if info.serial is not None else "  Serial: unknown")
    print(f"  Design capacity:{info.design_capacity_mah} mAh" if info.design_capacity_mah is not None else "  Design cap: unknown")
    print(f"  Full capacity:  {info.full_capacity_mah} mAh" if info.full_capacity_mah is not None else "  Full cap: unknown")
    print(f"  Cycle count:    {info.cycle_count}" if info.cycle_count is not None else "  Cycles: unknown")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="battery-clinic",
                                     description="Analyze/regenerate laptop batteries.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="read battery info")
    p_analyze.set_defaults(func=cmd_analyze)

    p_regen = sub.add_parser("regen", help="run a supervised calibration cycle")
    p_regen.add_argument("--index", type=int, default=0, help="battery index to use")
    p_regen.add_argument("--cycles", type=int, default=3, help="max cycles")
    p_regen.set_defaults(func=cmd_regen)

    p_smbus = sub.add_parser("smbus", help="probe a Smart Battery gauge (demo)")
    p_smbus.add_argument("--demo", action="store_true", help="use the fake in-memory bus")
    p_smbus.set_defaults(func=cmd_smbus)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
