"""PyInstaller / direct-run entry point for BatteryClinic."""
import sys

from battery_clinic.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
