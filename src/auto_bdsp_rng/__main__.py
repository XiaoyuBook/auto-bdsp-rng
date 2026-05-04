from __future__ import annotations

import argparse

from auto_bdsp_rng import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-bdsp-rng",
        description="BDSP blink seed detection and Gen 8 static RNG helper.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    build_parser().parse_args()
    print("auto_bdsp_rng startup entry is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
