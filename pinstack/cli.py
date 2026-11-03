"""Command-line interface for pinstack."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import pinstack
from pinstack.core import (
    CheckerRegistry,
    format_text,
    index_findings,
    run_checkers,
    scan_index,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_INDEX_SIZE,
)


def _build_registry() -> CheckerRegistry:
    from pinstack.checkers import ALL_CHECKERS

    reg = CheckerRegistry()
    for cls in ALL_CHECKERS:
        reg.register(cls)
    return reg


def _build_context_names(value: str) -> list[str]:
    """Parse ``--build-context NAME,NAME`` into validated, lower-cased names.

    :raises argparse.ArgumentTypeError: when a name is not a valid stage name.
    """
    from pinstack.checkers.dockerfile import build_context_name

    try:
        return [build_context_name(x) for x in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
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
        "--build-context",
        type=_build_context_names,
        action="extend",
        default=None,
        metavar="NAME,NAME",
        help=(
            "BuildKit named contexts the build supplies with"
            " 'docker buildx build --build-context NAME=...'; Dockerfiles may"
            " refer to these like stages (comma-separated, repeatable)"
        ),
    )
    parser.add_argument(
        "--list-checkers",
        action="store_true",
        default=False,
        help="Print available checkers and exit",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
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

    if args.build_context:
        from pinstack.checkers.dockerfile import DockerfileChecker

        for checker in checkers:
            if isinstance(checker, DockerfileChecker):
                checker.build_contexts = frozenset(args.build_context)

    # Collect patterns from the selected checkers directly
    patterns = set()
    for checker in checkers:
        patterns.update(checker.patterns)

    extra_exclude = set(args.exclude_dir) if args.exclude_dir else None
    scan = scan_index(
        root,
        patterns,
        max_depth=args.max_depth,
        max_index_size=args.max_files,
        extra_exclude_dirs=extra_exclude,
    )
    findings = index_findings(scan, args.max_depth, args.max_files)
    findings += run_checkers(checkers, scan.index, root)

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
