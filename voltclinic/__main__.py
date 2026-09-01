"""`python -m voltclinic` launches the GUI (falling back to CLI headless).

Prints are guarded because in a --windowed PyInstaller exe sys.stdout and
sys.stderr are None.
"""
import sys


def _eprint(msg: str) -> None:
    try:
        if sys.stderr is not None:
            print(msg, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from .gui.app import main as gui_main
        return gui_main()
    except Exception as exc:  # noqa: BLE001
        _eprint(f"[voltclinic] GUI failed to start ({exc}).")
        _eprint("Falling back to CLI. Use `python -m voltclinic.cli` for the CLI.")
        try:
            from .cli.__main__ import main as cli_main
            return cli_main(["analyze"])
        except SystemExit as e:
            return int(e.code or 0)
        except Exception:  # noqa: BLE001
            return 1


if __name__ == "__main__":
    sys.exit(main())
