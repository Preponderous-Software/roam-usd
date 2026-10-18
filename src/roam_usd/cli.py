"""Command-line interface: ``roam-usd export | generate | info``."""

from __future__ import annotations

import argparse
import logging
import sys

from roam_usd import __version__
from roam_usd.save import SaveFormatError, format_info, read_save_dir
from roam_usd.usd import ExportError, ExportOptions, export_world, output_format


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"roam-usd {args.command}: not yet implemented", file=sys.stderr)
    return 2


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
        world = read_save_dir(args.save)
        path = export_world(
            world, out, binary=args.binary, options=ExportOptions(levels=args.levels)
        )
    except (SaveFormatError, ExportError) as exc:
        print(f"roam-usd export: {exc}", file=sys.stderr)
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
    export.set_defaults(func=_cmd_export)

    generate = sub.add_parser(
        "generate", help="generate worlds from seeds with Roam's own generator, then export"
    )
    generate.set_defaults(func=_not_implemented)

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
