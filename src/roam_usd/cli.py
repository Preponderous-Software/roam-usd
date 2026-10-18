"""Command-line interface: ``roam-usd export | generate | info``."""

from __future__ import annotations

import argparse
import sys

from roam_usd import __version__


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"roam-usd {args.command}: not yet implemented", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roam-usd",
        description="Export worlds from the game Roam to OpenUSD stages.",
    )
    parser.add_argument("--version", action="version", version=f"roam-usd {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    export = sub.add_parser("export", help="export a Roam save folder to a USD stage")
    export.set_defaults(func=_not_implemented)

    generate = sub.add_parser(
        "generate", help="generate worlds from seeds with Roam's own generator, then export"
    )
    generate.set_defaults(func=_not_implemented)

    info = sub.add_parser("info", help="print rooms per level and entity counts per class")
    info.set_defaults(func=_not_implemented)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
