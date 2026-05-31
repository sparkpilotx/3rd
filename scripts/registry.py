# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "neo4j",
#     "pydantic",
#     "structlog",
# ]
# ///

import argparse
import sys

import structlog

from github_study.core import (
    AppError,
    GitHubRepoKey,
    print_error,
    print_json,
    read_neo4j_config_from_env,
)
from github_study.registry_store import (
    read_repository_records,
    write_initialize_registry,
    write_remove_repository,
    write_upsert_repository,
)

__all__ = ["main"]

structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _write_human(message: str) -> None:
    print(message, file=sys.stderr)


def _cmd_init(*, as_json: bool) -> int:
    try:
        result = write_initialize_registry(read_neo4j_config_from_env())
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(result.model_dump())
    else:
        _write_human(f"initialized registry database: {result.database}")
    return 0


def _cmd_add(raw_key: str, *, as_json: bool) -> int:
    try:
        result = write_upsert_repository(
            read_neo4j_config_from_env(),
            GitHubRepoKey.parse(raw_key),
        )
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(result.model_dump())
    else:
        _write_human(f"registered: {result.key}")
    return 0


def _cmd_remove(raw_key: str, *, as_json: bool) -> int:
    try:
        key = GitHubRepoKey.parse(raw_key)
        result = write_remove_repository(read_neo4j_config_from_env(), key)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(result.model_dump())
    else:
        _write_human(f"removed: {key.value}")
    return 0


def _cmd_list(*, as_json: bool) -> int:
    try:
        records = read_repository_records(read_neo4j_config_from_env())
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json([record.model_dump() for record in records])
    else:
        for record in records:
            _write_human(record.key)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage the Neo4j source of truth for GitHub study repositories"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Output results as JSON"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the registry database and constraints")

    add_parser = subparsers.add_parser("add", help="Register a GitHub repository key")
    add_parser.add_argument("key", help="GitHub repository key as owner/repo")

    remove_parser = subparsers.add_parser(
        "remove", help="Remove a GitHub repository key from the registry"
    )
    remove_parser.add_argument("key", help="GitHub repository key as owner/repo")

    subparsers.add_parser("list", help="List registered repository keys")

    args = parser.parse_args()

    match args.command:
        case "init":
            return _cmd_init(as_json=args.as_json)
        case "add":
            return _cmd_add(args.key, as_json=args.as_json)
        case "remove":
            return _cmd_remove(args.key, as_json=args.as_json)
        case "list":
            return _cmd_list(as_json=args.as_json)
        case _:
            parser.print_help(sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
