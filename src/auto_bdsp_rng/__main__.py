from __future__ import annotations

import argparse
import json
import sys

from auto_bdsp_rng import __version__
from auto_bdsp_rng.blink_detection import (
    ProjectXsIntegrationError,
    SeedState32,
    advance_seed_state,
    capture_player_blinks,
    load_project_xs_config,
    reidentify_seed_from_observation,
    recover_seed_from_observation,
    save_eye_preview,
    save_preview_frame,
)


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

    preview_eye = subparsers.add_parser(
        "preview-eye",
        help="Capture one frame, draw eye-template matching preview, and save it.",
    )
    preview_eye.add_argument(
        "--project-xs-config",
        required=True,
        help="Project_Xs config file name or absolute JSON path.",
    )
    preview_eye.add_argument(
        "--output",
        required=True,
        help="Output annotated image path.",
    )

    capture_blinks = subparsers.add_parser(
        "capture-blinks",
        help="Capture player blinks through Project_Xs and recover Seed[0-3]/Seed[0-1].",
    )
    capture_blinks.add_argument(
        "--project-xs-config",
        required=True,
        help="Project_Xs config file name or absolute JSON path.",
    )
    capture_blinks.add_argument(
        "--blink-count",
        type=int,
        default=40,
        help="Blink count to capture before seed recovery.",
    )
    capture_blinks.add_argument(
        "--npc",
        type=int,
        default=None,
        help="Override npc count from the Project_Xs config.",
    )

    reidentify = subparsers.add_parser(
        "reidentify",
        help="Capture later blink intervals and reidentify an existing Seed[0-3].",
    )
    reidentify.add_argument(
        "--project-xs-config",
        required=True,
        help="Project_Xs config file name or absolute JSON path.",
    )
    reidentify.add_argument(
        "--seed",
        nargs=4,
        required=True,
        metavar=("S0", "S1", "S2", "S3"),
        help="Existing Seed[0-3] as four hexadecimal 32-bit words.",
    )
    reidentify.add_argument(
        "--blink-count",
        type=int,
        default=7,
        help="Blink interval count to capture for reidentify.",
    )
    reidentify.add_argument(
        "--npc",
        type=int,
        default=None,
        help="Override npc count from the Project_Xs config.",
    )
    reidentify.add_argument(
        "--search-min",
        type=int,
        default=0,
        help="Minimum advance to search.",
    )
    reidentify.add_argument(
        "--search-max",
        type=int,
        default=1_000_000,
        help="Maximum advance to search.",
    )

    advance_seed = subparsers.add_parser(
        "advance-seed",
        help="Manually advance an existing Seed[0-3] through Project_Xs Xorshift.",
    )
    advance_seed.add_argument(
        "--seed",
        nargs=4,
        required=True,
        metavar=("S0", "S1", "S2", "S3"),
        help="Existing Seed[0-3] as four hexadecimal 32-bit words.",
    )
    advance_seed.add_argument(
        "--advances",
        type=int,
        required=True,
        help="Number of advances to apply.",
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
    if args.command == "preview-eye":
        try:
            config = load_project_xs_config(args.project_xs_config)
            output, preview = save_eye_preview(config.capture, args.output)
        except ProjectXsIntegrationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"output": str(output), **preview.as_dict()}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "capture-blinks":
        try:
            config = load_project_xs_config(args.project_xs_config, blink_count=args.blink_count)
            observation = capture_player_blinks(config.capture)
            npc = config.npc if args.npc is None else args.npc
            result = recover_seed_from_observation(observation, npc=npc)
        except ProjectXsIntegrationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"npc": npc, **result.as_dict()}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "reidentify":
        try:
            state = SeedState32.from_hex_words(args.seed)
            config = load_project_xs_config(args.project_xs_config, blink_count=args.blink_count)
            observation = capture_player_blinks(config.capture)
            npc = config.npc if args.npc is None else args.npc
            result = reidentify_seed_from_observation(
                state,
                observation,
                npc=npc,
                search_min=args.search_min,
                search_max=args.search_max,
            )
        except (ProjectXsIntegrationError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"npc": npc, **result.as_dict()}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "advance-seed":
        try:
            state = SeedState32.from_hex_words(args.seed)
            result = advance_seed_state(state, args.advances)
        except (ProjectXsIntegrationError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    print("auto_bdsp_rng startup entry is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
