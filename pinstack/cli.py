"""Command-line interface for pinstack."""

import argparse
import os
import sys
from typing import List, Optional

import pinstack
from pinstack.core import (
    CheckerRegistry,
    build_index,
    format_text,
    run_checkers,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_INDEX_SIZE,
)


def _build_registry():
    # type: () -> CheckerRegistry
    from pinstack.checkers import ALL_CHECKERS
    reg = CheckerRegistry()
    for cls in ALL_CHECKERS:
        reg.register(cls)
    return reg


def _build_parser():
    # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser(
        prog="pinstack",
        description="Supply-chain dependency pinning enforcement.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Root directory to scan (default: .)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="pinstack {}".format(pinstack.__version__),
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        type=lambda s: [x.strip() for x in s.split(",")],
        default=None,
        metavar="NAME,NAME",
        help="Only run these checkers",
    )
    group.add_argument(
        "--exclude",
        type=lambda s: [x.strip() for x in s.split(",")],
        default=None,
        metavar="NAME,NAME",
        help="Skip these checkers",
    )

    parser.add_argument(
        "--format",
        choices=["text", "sarif"],
        default="text",
        dest="output_format",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        default=False,
        help="Always exit 0 regardless of findings",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        metavar="N",
        help="Max directory depth to traverse (default: {})".format(DEFAULT_MAX_DEPTH),
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_INDEX_SIZE,
        metavar="N",
        help="Max files to index (default: {})".format(DEFAULT_MAX_INDEX_SIZE),
    )
    parser.add_argument(
        "--exclude-dir",
        type=lambda s: [x.strip() for x in s.split(",")],
        default=None,
        metavar="DIR,DIR",
        help="Additional directory names to skip during traversal (comma-separated)",
    )
    parser.add_argument(
        "--list-checkers",
        action="store_true",
        default=False,
        help="Print available checkers and exit",
    )
    return parser


def main(argv=None):
    # type: (Optional[List[str]]) -> None
    parser = _build_parser()
    args = parser.parse_args(argv)

    registry = _build_registry()

    if args.list_checkers:
        for checker in registry.get_all():
            sys.stdout.write("{}  {}\n".format(checker.name, checker.description))
        sys.exit(0)

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        sys.stderr.write("Error: {} is not a directory\n".format(root))
        sys.exit(2)

    try:
        if args.check:
            checkers = registry.get_by_names(args.check)
        elif args.exclude:
            checkers = registry.get_all(exclude=args.exclude)
        else:
            checkers = registry.get_all()
    except ValueError as exc:
        sys.stderr.write("Error: {}\n".format(exc))
        sys.exit(2)

    # Collect patterns from the selected checkers directly
    patterns = set()
    for checker in checkers:
        patterns.update(checker.patterns)

    extra_exclude = set(args.exclude_dir) if args.exclude_dir else None
    index = build_index(
        root,
        patterns,
        max_depth=args.max_depth,
        max_index_size=args.max_files,
        extra_exclude_dirs=extra_exclude,
    )
    findings = run_checkers(checkers, index, root)

    if args.output_format == "sarif":
        from pinstack.sarif import format_sarif
        sys.stdout.write(format_sarif(findings))
        sys.stdout.write("\n")
    else:
        output = format_text(findings)
        sys.stdout.write(output)
        sys.stdout.write("\n")

    if args.exit_zero:
        sys.exit(0)
    elif findings:
        sys.exit(1)
    else:
        sys.exit(0)
