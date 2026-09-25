from __future__ import annotations

import argparse
from collections.abc import Sequence

if __package__:
    from .database import init_mysql_database
else:
    from database import init_mysql_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UTS CS Career Navigator database management")
    subcommands = parser.add_subparsers(dest="command", required=True)
    migrate_parser = subcommands.add_parser("migrate", help="apply idempotent schema updates")
    migrate_parser.add_argument(
        "--create-database",
        action="store_true",
        help="create MYSQL_DATABASE first (local setup only; omit for managed cloud databases)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        init_mysql_database(create_database=args.create_database)
        print("Database migration completed successfully.")
        return 0

    # argparse currently prevents this path; keeping it explicit makes the
    # return contract clear if more commands are added later.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
