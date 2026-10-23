"""Command-line interface: ``roam-usd export | generate | info``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from roam_usd import __version__
from roam_usd.generate import (
    MAX_CAVE_DEPTH,
    GenerateError,
    generate_batch,
    generate_seed,
    parse_seeds,
)
from roam_usd.save import (
    SaveFormatError,
    format_info,
    read_bundle,
    read_bundle_world,
    read_save,
)
from roam_usd.usd import ExportError, ExportOptions, export_world, output_format

SAVE_HELP = (
    "a Roam save folder (e.g. saves/defaultsavefile) or a browser save bundle "
    "(roam-saves-<date>.json)"
)


def _bundle_listing(path: str) -> str:
    worlds = read_bundle(path)
    lines = [f"Bundle: {path}", f"Worlds: {len(worlds)}"]
    for name in worlds:
        rooms = len(read_bundle_world(path, name).rooms())
        lines.append(f"  {name}: {rooms} room{'s' if rooms != 1 else ''}")
    if len(worlds) > 1:
        lines.append("Choose one with --world NAME for details or to export it.")
    return "\n".join(lines)


def _cmd_info(args: argparse.Namespace) -> int:
    try:
        if Path(args.save).is_file() and args.world is None:
            print(_bundle_listing(args.save))
            return 0
        world = read_save(args.save, args.world)
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
        world = read_save(args.save, args.world)
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
    common = dict(
        radius=args.radius,
        cave_depth=args.cave_depth,
        home=not args.no_home,
        binary=args.binary,
        roam_python=args.roam_python,
        keep_save=args.keep_saves,
    )
    try:
        if args.seeds is not None:
            try:
                seeds = parse_seeds(args.seeds)
            except ValueError as exc:
                raise GenerateError(f"--seeds: {exc}") from None
            _, index = generate_batch(
                args.roam,
                seeds,
                args.output,
                progress=lambda path: print(f"wrote {path}", flush=True),
                **common,
            )
            print(f"wrote {index}")
        else:
            print(f"wrote {generate_seed(args.roam, args.seed, args.output, **common)}")
    except (GenerateError, SaveFormatError, ExportError) as exc:
        print(f"roam-usd generate: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roam-usd",
        description="Export worlds from the game Roam to OpenUSD stages.",
    )
    parser.add_argument("--version", action="version", version=f"roam-usd {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    export = sub.add_parser(
        "export",
        help="export a Roam save folder, or one world of a browser save bundle, to a USD stage",
    )
    export.add_argument("save", help=SAVE_HELP)
    export.add_argument(
        "--world", metavar="NAME", help="the world to read from a browser save bundle"
    )
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
    which = generate.add_mutually_exclusive_group(required=True)
    which.add_argument("--seed", type=int, help="generate one world from this seed")
    which.add_argument(
        "--seeds",
        metavar="A..B|A,B,C",
        help="generate one world per seed, plus index.usda showing them side by side; "
        "write a list that starts with a negative seed with '=', e.g. --seeds=-2..1",
    )
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

    info = sub.add_parser(
        "info",
        help="print the tick, rooms per level, entity counts per class and the player "
        "position; for a bundle, list its worlds",
    )
    info.add_argument("save", help=SAVE_HELP)
    info.add_argument(
        "--world", metavar="NAME", help="the world to read from a browser save bundle"
    )
    info.set_defaults(func=_cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(format="roam-usd: warning: %(message)s", level=logging.WARNING)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
