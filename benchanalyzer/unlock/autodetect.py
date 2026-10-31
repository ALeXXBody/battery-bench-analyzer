"""USB adapter auto-detection for the connection bar.

Asks each known adapter family whether hardware is currently present and
returns friendly labels. Used so the GUI can auto-select a connected
adapter instead of the operator having to know the combo box ordering.
"""

from __future__ import annotations

ADAPTER_PRIORITY = ("cp2112", "mcp2221", "rotool")

_ADAPTOR_LABELS = {
    "cp2112": "CP2112 USB-SMBus adapter",
    "mcp2221": "MCP2221A USB-SMBus adapter",
    "rotool": "ROTOOL USB analyzer",
}


def label_for(adapter: str) -> str:
    """Friendly combo-box text for an adapter key."""
    return _ADAPTOR_LABELS.get(adapter, adapter)


def detect_adapters() -> list[str]:
    """Return adapter keys currently connected via USB, priority order."""
    import platform

    system = platform.system()

    found: list[str] = []

    if system == "Windows":
        # HID-based adapters (CP2112, MCP2221A): enumerate by exact VID:PID.
        try:
            from .cp2112 import CP2112_PID, CP2112_VID, HidApi

            api = HidApi()
            have = {(v, i) for (_, v, i) in api.enumerate_hid_paths()}
            if (CP2112_VID, CP2112_PID) in have:
                found.append("cp2112")
            if (0x04D8, 0x00DD) in have:
                found.append("mcp2221")
        except Exception:  # noqa: BLE001 - detection must never crash the UI
            pass

    # Serial-bridge adapters work on every platform via pyserial.
    try:
        from .rotool import list_rotool_ports

        if list_rotool_ports():
            found.append("rotool")
    except Exception:  # noqa: BLE001
        pass

    return sorted(set(found), key=ADAPTER_PRIORITY.index)
