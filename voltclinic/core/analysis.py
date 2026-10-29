"""Pack analysis: NLBA-style verdict computed from SBS data + cell readings.

Read-only arithmetic over already-fetched information. Per-cell voltages
themselves come from consent-gated TI ManufacturerAccess reads elsewhere
(bq40z-class DAStatus subcommands); this module only interprets numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PackAnalysis:
    soh_percent: Optional[float] = None
    health_class: str = "unknown"
    capacity_loss_percent: Optional[float] = None
    cells_style: int = 0                    # series cell count estimate
    cell_voltages_mv: List[float] = field(default_factory=list)
    imbalance_mv: Optional[float] = None
    imbalance_state: str = "unknown"
    findings: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    verdict: str = ""

    def render(self) -> str:
        lines = [self.verdict]
        lines += [f"• {f}" for f in self.findings]
        lines += [f"⚠ {w}" for w in self.warnings]
        return "\n".join(lines)


def estimate_cell_count(design_voltage_mv: Optional[float]) -> int:
    """Series count from DesignVoltage (Li-ion nominal 3.7 V)."""
    if not design_voltage_mv:
        return 0
    n = round(design_voltage_mv / 3700.0)
    return n if 1 <= n <= 14 else 0


def _health_classification(soh: Optional[float]) -> str:
    if soh is None:
        return "unknown"
    if soh >= 92.0:
        return "excellent"
    if soh >= 80.0:
        return "good"
    if soh >= 60.0:
        return "worn"
    return "poor"


def classify_imbalance(delta_mv: Optional[float]) -> str:
    if delta_mv is None:
        return "unknown"
    if delta_mv <= 20:
        return "balanced"
    if delta_mv <= 50:
        return "acceptable drift"
    if delta_mv <= 100:
        return "needs balancing"
    return "severely unbalanced"


def analyse(info, cell_voltages_mv: Optional[List[float]] = None,
            temperature_c: Optional[float] = None) -> PackAnalysis:
    """Build the operator-facing analysis from a BatteryInfo snapshot."""
    a = PackAnalysis()
    design = info.raw.get("design_capacity_mah")
    full = info.raw.get("full_charge_capacity_mah")

    if design and full:
        a.soh_percent = 100.0 * full / design
        a.capacity_loss_percent = 100.0 - a.soh_percent
        a.health_class = _health_classification(a.soh_percent)
    a.cells_style = estimate_cell_count(info.design_voltage_mv)
    cells = cell_voltages_mv or []
    a.cell_voltages_mv = list(cells)
    if len(cells) >= 2:
        a.imbalance_mv = max(cells) - min(cells)
        a.imbalance_state = classify_imbalance(a.imbalance_mv)

    if a.health_class != "unknown":
        a.findings.append(
            f"capacity retention {a.soh_percent:.1f}% "
            f"({a.capacity_loss_percent:.1f}% left on the table)")
    if info.cycle_count is not None:
        if info.cycle_count >= 800:
            a.warnings.append(
                f"{info.cycle_count} cycles recorded — near or past "
                "consumer pack design life")
        else:
            a.findings.append(f"{info.cycle_count} charge cycles")
    if a.imbalance_state == "balanced":
        a.findings.append(
            f"cells balanced (largest gap {a.imbalance_mv:.0f} mV)")
    elif a.imbalance_state != "unknown":
        a.warnings.append(f"cell imbalance: {a.imbalance_state}")
    if cells and a.cells_style and len(cells) != a.cells_style:
        a.warnings.append(
            f"gauge reports {len(cells)} cell voltages but design voltage "
            f"implies {a.cells_style}S — check the DAStatus mapping")
    if cells:
        for i, v in enumerate(cells, start=1):
            if not (2500 <= v <= 4300):
                a.warnings.append(
                    f"cell {i} at {v:.0f} mV is outside the healthy Li-ion "
                    "idle window")
    if temperature_c is not None:
        if temperature_c > 45:
            a.warnings.append(
                f"pack at {temperature_c:.0f} °C — above charge-safe range")
        elif temperature_c < 0:
            a.warnings.append(
                f"pack at {temperature_c:.0f} °C — charging below 0 °C "
                "plates lithium; warm it to room temperature first")
        else:
            a.findings.append(f"temperature {temperature_c:.1f} °C — normal")

    if not a.findings:
        a.findings.append("not enough answered registers for a full verdict")
    if a.warnings:
        a.verdict = ("Analysis: usable with issues" if a.health_class in
                     ("good", "worn") else "Analysis: issues detected")
    elif a.health_class in ("excellent", "good", "worn"):
        a.verdict = f"Analysis: pack looks {a.health_class}"
    else:
        a.verdict = "Analysis: incomplete data"
    return a
