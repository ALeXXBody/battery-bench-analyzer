"""`python -m battery_clinic` launches the GUI (falling back to CLI headless)."""
import sys


def main() -> int:
    try:
        from .gui.app import main as gui_main
        return gui_main()
    except Exception as exc:  # noqa: BLE001
        print(f"[battery_clinic] GUI failed to start ({exc}).", file=sys.stderr)
        print("Falling back to CLI. Use `python -m battery_clinic.cli` for the CLI.", file=sys.stderr)
        from .cli.__main__ import main as cli_main
        return cli_main(["analyze"])


if __name__ == "__main__":
    sys.exit(main())
