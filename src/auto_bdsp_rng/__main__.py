from __future__ import annotations

import argparse
import json
import sys

from auto_bdsp_rng import __version__
from auto_bdsp_rng.blink_detection import ProjectXsIntegrationError, load_project_xs_config, save_preview_frame


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
    subparsers = parser.add_subparsers(dest="command")

    blink_config = subparsers.add_parser(
        "blink-config",
        help="Load and print a Project_Xs blink config without starting capture.",
    )
    blink_config.add_argument(
        "--project-xs-config",
        required=True,
        help="Project_Xs config file name or absolute JSON path.",
    )
    blink_config.add_argument(
        "--blink-count",
        type=int,
        default=40,
        help="Blink count to request when capture is later started.",
    )

    capture_frame = subparsers.add_parser(
        "capture-frame",
        help="Capture one preview frame from a Project_Xs config and save it.",
    )
    capture_frame.add_argument(
        "--project-xs-config",
        required=True,
        help="Project_Xs config file name or absolute JSON path.",
    )
    capture_frame.add_argument(
        "--output",
        required=True,
        help="Output image path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "blink-config":
        try:
            config = load_project_xs_config(args.project_xs_config, blink_count=args.blink_count)
        except ProjectXsIntegrationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(config.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "capture-frame":
        try:
            config = load_project_xs_config(args.project_xs_config)
            output = save_preview_frame(config.capture, args.output)
        except ProjectXsIntegrationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"saved preview frame: {output}")
        return 0

    print("auto_bdsp_rng startup entry is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
