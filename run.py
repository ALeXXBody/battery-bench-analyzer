"""PyInstaller / direct-run entry point for VoltClinic."""
import sys

from voltclinic.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
