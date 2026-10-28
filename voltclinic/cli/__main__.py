"""Headless CLI for bench battery analysis, calibration and legacy flows.

Bench commands (battery out of the device, wired to an adapter):
  analyze    --adapter demo|cp2112 [--addr 0x0B] [--watch SECONDS]
  calibrate  --adapter demo|cp2112 [--addr 0x0B] [--low 10]

Legacy laptop commands (battery inside this machine):
  analyze --internal
  regen   --cycles N
"""
from __future__ import annotations

import argparse
import sys
import time

from ..core.bench import BenchCalibration, FakeGaugeBus, GaugeSession
from ..core.models import BatteryInfo
from ..core.reader import BatteryReader
from ..core.regeneration import RegenerationEngine
from ..core.sbs import scan_for_gauge
from ..unlock.smbus import (
    FakeSMbusBus,
    SMbusError,
    SMbusBus,
    SB_CYCLE_COUNT,
    SB_DESIGN_CAPACITY,
    SB_DEVICE_NAME,
    SB_FULL_CHARGE_CAPACITY,
    SB_MANUFACTURER_NAME,
    SB_SERIAL_NUMBER,
)


def _print_battery(info: BatteryInfo) -> None:
    for line in info.summary_lines():
        print(line)


def _demo_bus() -> FakeGaugeBus:
    return FakeGaugeBus()


def _open_bench(args) -> GaugeSession:
    return GaugeSession.open(args.adapter, args.addr)


def cmd_analyze(args) -> int:
    if args.internal:
        reader = BatteryReader()
        batteries = reader.refresh()
        if not batteries:
            print("No internal batteries found.")
            print(f"Backend: {reader.backend_name} (only usable on a Windows "
                  "laptop with a battery).")
            return 1
        for i, b in enumerate(batteries):
            print(f"=== Battery {i + 1} ===")
            _print_battery(b)
            print()
        return 0

    try:
        session = _open_bench(args)
    except SMbusError as exc:
        print(f"Bench connect failed: {exc}")
        print("Tip: wire the pack SMBus (SCL/SDA/GND) to the adapter, or use "
              "--adapter demo / --internal.")
        return 1

    print(f"Adapter: {session.adapter_name}  gauge: 0x{session.addr:02X}")
    try:
        info = session.snapshot()
    except SMbusError as exc:
        print(f"Read failed: {exc}")
        return 1
    _print_battery(info)
    print(f"Voltage:        {info.voltage_mv and round(info.voltage_mv)} mV")
    print(f"Current:        {info.current_ma and round(info.current_ma)} mA")
    print(f"Temperature:    "
          f"{info.temperature_c and round(info.temperature_c, 1)} C")
    soc_txt = f"{info.relative_soc:.0f}%" if info.relative_soc is not None else "Unknown"
    print(f"Charge (RSOC):  {soc_txt}")
    print(f"Status flags:   {info.raw.get('status_flags', 'unknown')}")

    if args.watch:
        g = session.gauge
        print(f"\nLive telemetry for {args.watch}s (1s interval):")
        try:
            for i in range(args.watch):
                v = g.voltage_mv()
                cur = g.current_ma()
                t = g.temperature_c()
                soc = g.relative_soc()
                flags = " ".join(g.status_flags()).lower() or "idle"
                print(f"  {i:3d}s  V={v and round(v)}mV  I={cur and round(cur)}mA  "
                      f"T={t and round(t, 1)}C  SoC={soc and round(soc)}%  [{flags}]")
                if i < args.watch - 1:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\n(stopped)")
    session.close()
    return 0


def cmd_calibrate(args) -> int:
    try:
        session = _open_bench(args)
    except SMbusError as exc:
        print(f"Bench connect failed: {exc}")
        return 1
    demo = session.adapter_name == "demo"
    power = session.bus.power if demo else None
    if not demo:
        print("Guided learn cycle. YOU control the bench power when prompted:")
        print("  1. connect charger -> charge to full")
        print(f"  2. connect load    -> discharge to {args.low}%")
        print("  3. recharge to full")
        print("The app only monitors the gauge. Never leave a pack unattended.\n")
    engine = BenchCalibration(session.gauge, power_control=power,
                              low_rsoc=args.low, poll_s=1.0)

    def progress(msg, frac):
        print(f"  [{'' if frac is None else f'{frac*100:5.1f}%'}] {msg}")

    try:
        result = engine.run(progress=progress)
    except KeyboardInterrupt:
        engine.cancel()
        print("\nCancel requested.")
        return 130
    print("\nLearn cycle finished." + (" (cancelled)" if result.cancelled else ""))
    print(f"  FCC before: {result.fcc_before}")
    print(f"  FCC after:  {result.fcc_after}")
    if result.soh_before is not None and result.soh_after is not None:
        print(f"  SoH before: {result.soh_before:.1f}%")
        print(f"  SoH after:  {result.soh_after:.1f}%")
    session.close()
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
    elif args.adapter == "cp2112":
        from ..unlock.cp2112 import Cp2112Smbus
        try:
            bus = Cp2112Smbus()
        except Exception as e:  # e.g. not on Windows / no adapter present
            print(f"CP2112 adapter unavailable: {e}")
            return 1
        print("[CP2112 USB-SMBus adapter]")
    elif args.adapter == "rotool":
        from ..unlock.rotool import RoToolSmbus
        try:
            bus = RoToolSmbus()
        except Exception as e:  # e.g. no device / protocol pending
            print(f"ROTOOL adapter unavailable: {e}")
            return 1
        print("[ROTOOL USB analyzer]")
    else:
        print("Use --demo for the fake gauge, or --adapter cp2112/rotool for a "
              "real USB adapter.")
        return 1

    addr = int(args.addr, 16)
    found = scan_for_gauge(bus) if args.scan else addr
    if found is None:
        print("No SBS gauge answered on any known address.")
        return 1
    from ..unlock.smbus import FuelGaugeInterop
    gauge = FuelGaugeInterop(bus, found)
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


def cmd_regen(args) -> int:
    reader = BatteryReader()
    batteries = reader.refresh()
    if not batteries:
        print("No internal batteries found to regenerate.")
        print("(For a bench battery use: voltclinic calibrate --adapter ...)")
        return 1
    engine = RegenerationEngine(reader, battery_index=args.index,
                                max_cycles=args.cycles)
    print(f"Starting internal-battery regeneration (max {args.cycles} cycles). "
          f"Keep an eye on the machine. Ctrl+C to stop safely at endpoints.")
    result = engine.run(progress=lambda msg, frac: print(f"  [{frac*100:5.1f}%] {msg}"))
    print("\nRegeneration finished.")
    print(f"  Cycles: {result.cycles_completed}")
    b = result.before_soh
    a = result.after_soh
    print(f"  SoH before: {f'{b:.1f}%' if b is not None else 'unknown'}")
    print(f"  SoH after:  {f'{a:.1f}%' if a is not None else 'unknown'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="voltclinic",
                                     description="Bench battery analyzer: "
                                     "test, repair and calibrate pack gauges over SMBus.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze the bench battery via adapter")
    adapter = p_analyze.add_mutually_exclusive_group()
    adapter.add_argument("--internal", action="store_true",
                         help="read THIS machine's battery instead of the bench")
    p_analyze.add_argument("--adapter", choices=["demo", "cp2112", "rotool"], default="demo")
    p_analyze.add_argument("--addr", type=lambda s: int(s, 16), default=0x0B,
                           help="gauge address, default 0x0B")
    p_analyze.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                           help="print live telemetry for N seconds")
    p_analyze.set_defaults(func=cmd_analyze)

    p_cal = sub.add_parser("calibrate", help="guided learn cycle on the bench battery")
    p_cal.add_argument("--adapter", choices=["demo", "cp2112", "rotool"], default="demo")
    p_cal.add_argument("--addr", type=lambda s: int(s, 16), default=0x0B)
    p_cal.add_argument("--low", type=float, default=10.0, help="discharge floor %%RSOC")
    p_cal.set_defaults(func=cmd_calibrate)

    p_regen = sub.add_parser("regen", help="recalibrate THIS laptop's internal battery")
    p_regen.add_argument("--index", type=int, default=0, help="battery index to use")
    p_regen.add_argument("--cycles", type=int, default=3, help="max cycles")
    p_regen.set_defaults(func=cmd_regen)

    p_smbus = sub.add_parser("smbus", help="probe a Smart Battery gauge")
    grp = p_smbus.add_mutually_exclusive_group()
    grp.add_argument("--demo", action="store_true", help="use the fake in-memory bus")
    grp.add_argument("--adapter", choices=["cp2112", "rotool"], default="cp2112",
                     help="real SMBus adapter driver (Windows HID)")
    p_smbus.add_argument("--addr", default="0x0B", help="gauge address, default 0x0B")
    p_smbus.add_argument("--scan", action="store_true",
                         help="scan common addresses instead of using --addr")
    p_smbus.set_defaults(func=cmd_smbus)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())