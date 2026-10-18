"""Command-line interface: ``roam-usd export | generate | info``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from roam_usd import __version__
from roam_usd.generate import MAX_CAVE_DEPTH, GenerateError, generate_seed
from roam_usd.save import SaveFormatError, format_info, read_save_dir
from roam_usd.usd import ExportError, ExportOptions, export_world, output_format


def _cmd_info(args: argparse.Namespace) -> int:
    try:
        world = read_save_dir(args.save)
    except SaveFormatError as exc:
        print(f"roam-usd info: {exc}", file=sys.stderr)
        return 1
    print(format_info(world))
    return 0


def _default_output(binary: bool) -> str:
    return "world.usdc" if binary else "world.usda"


def _cmd_export(args: argparse.Namespace) -> int:
    out = args.output or _default_output(args.binary)
    try:
        output_format(out, args.binary)
        if args.roam is not None and not Path(args.roam).is_dir():
            raise ExportError(f"--roam {args.roam}: not a directory")
        world = read_save_dir(args.save)
        options = ExportOptions(
            levels=args.levels,
            roam_checkout=Path(args.roam).resolve() if args.roam else None,
        )
        path = export_world(world, out, binary=args.binary, options=options)
    except (SaveFormatError, ExportError) as exc:
        print(f"roam-usd export: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        path = generate_seed(
            args.roam,
            args.seed,
            args.output,
            radius=args.radius,
            cave_depth=args.cave_depth,
            home=not args.no_home,
            binary=args.binary,
            roam_python=args.roam_python,
            keep_save=args.keep_saves,
        )
    except (GenerateError, SaveFormatError, ExportError) as exc:
        print(f"roam-usd generate: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roam-usd",
        description="Export worlds from the game Roam to OpenUSD stages.",
    )
    parser.add_argument("--version", action="version", version=f"roam-usd {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    export = sub.add_parser("export", help="export a Roam save folder to a USD stage")
    export.add_argument("save", help="a Roam save folder, e.g. saves/defaultsavefile")
    export.add_argument(
        "-o", "--output", help="output stage (default: world.usda, or world.usdc with --binary)"
    )
    export.add_argument("--binary", action="store_true", help="write binary .usdc instead of .usda")
    export.add_argument(
        "--levels",
        choices=("all", "surface"),
        default="all",
        help="export every level (default) or only the surface",
    )
    export.add_argument(
        "--roam",
        metavar="ROAM_CHECKOUT",
        help="your Roam clone; creature sprite cards are textured from its PNGs",
    )
    export.set_defaults(func=_cmd_export)

    generate = sub.add_parser(
        "generate", help="generate worlds from seeds with Roam's own generator, then export"
    )
    generate.add_argument(
        "--roam", required=True, metavar="ROAM_CHECKOUT", help="your local clone of Roam"
    )
    generate.add_argument("--seed", type=int, required=True, help="the world's seed")
    generate.add_argument(
        "--radius", type=int, default=2, help="rooms within this distance of (0, 0) (default 2)"
    )
    generate.add_argument(
        "--cave-depth",
        type=int,
        default=MAX_CAVE_DEPTH,
        choices=range(0, MAX_CAVE_DEPTH + 1),
        metavar="D",
        help=f"cave levels to generate below cave entrances, 0-{MAX_CAVE_DEPTH} "
        f"(default {MAX_CAVE_DEPTH})",
    )
    generate.add_argument(
        "--no-home", action="store_true", help="do not build the starting home in room (0, 0)"
    )
    generate.add_argument("--binary", action="store_true", help="write .usdc instead of .usda")
    generate.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    generate.add_argument(
        "--roam-python",
        metavar="PYTHON",
        help="interpreter with Roam's requirements installed (default: this one)",
    )
    generate.add_argument(
        "--keep-saves",
        action="store_true",
        help="keep each generated Roam save in <output>/seed_<n>_save/",
    )
    generate.set_defaults(func=_cmd_generate)

    info = sub.add_parser("info", help="print rooms per level and entity counts per class")
    info.add_argument("save", help="a Roam save folder, e.g. saves/defaultsavefile")
    info.set_defaults(func=_cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(format="roam-usd: warning: %(message)s", level=logging.WARNING)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
