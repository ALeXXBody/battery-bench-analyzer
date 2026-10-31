"""PyInstaller / direct-run entry point for Battery Bench Analyzer."""
import sys

from benchanalyzer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
