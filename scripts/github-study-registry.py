# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "neo4j",
#     "pydantic",
#     "structlog",
# ]
# ///

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from enum import StrEnum
from typing import LiteralString, NoReturn, cast

import structlog
from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, ConfigDict

__all__ = ["main"]

structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
log = structlog.get_logger()

_DEFAULT_DATABASE = "workspace-3rd"
_DATABASE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_KEY_PATTERN = re.compile(r"^[^/\s]+/[^/\s]+$")


class ErrorCode(StrEnum):
    EXTERNAL = "EXTERNAL"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_ENV = "MISSING_ENV"


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        context: dict[str, object] | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.context = context or {}
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, object]:
        return {
            "error": self.code,
            "message": self.message,
            "context": self.context,
            **({"suggestion": self.suggestion} if self.suggestion else {}),
        }


class Neo4jConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    uri: str
    username: str
    password: str
    database: str


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    key: str
    createdAt: str | None = None


class RegistryResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    database: str
    count: int


def _load_neo4j_config() -> Neo4jConfig:
    missing = [
        v
        for v in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
        if not os.environ.get(v)
    ]
    if missing:
        raise AppError(
            ErrorCode.MISSING_ENV,
            "Missing required environment variables",
            context={"variables": missing},
            suggestion="export " + " ".join(f"{v}=<value>" for v in missing),
        )

    database = os.environ.get("NEO4J_DATABASE", _DEFAULT_DATABASE)
    if not _DATABASE_PATTERN.fullmatch(database):
        raise AppError(
            ErrorCode.INVALID_INPUT,
            "Invalid Neo4j database name",
            context={"database": database},
            suggestion="Use letters, numbers, underscore, dot, or hyphen in NEO4J_DATABASE.",
        )

    return Neo4jConfig(
        uri=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
        database=database,
    )


def _repository_record_from_key(key: str) -> RepositoryRecord:
    if not _KEY_PATTERN.fullmatch(key):
        raise AppError(
            ErrorCode.INVALID_INPUT,
            "Invalid GitHub repository key",
            context={"key": key},
            suggestion="Pass a key in owner/repo form.",
        )

    return RepositoryRecord(key=key)


def _database_identifier(database: str) -> str:
    return f"`{database}`"


def _trusted_dynamic_cypher(statement: str) -> LiteralString:
    # Database identifiers are validated before interpolation; Neo4j DDL does not support parameters.
    return cast(LiteralString, statement)


def write_initialize_registry(config: Neo4jConfig) -> RegistryResult:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    try:
        try:
            with driver.session(database="system") as session:  # pyright: ignore[reportUnknownMemberType]
                log.info("create_database", database=config.database)
                session.run(
                    Query(
                        _trusted_dynamic_cypher(
                            f"CREATE DATABASE {_database_identifier(config.database)} IF NOT EXISTS WAIT"
                        )
                    )
                ).consume()
        except Neo4jError as err:
            raise AppError(
                ErrorCode.EXTERNAL,
                "Could not create or verify Neo4j database",
                context={"database": config.database, "error": str(err)},
                suggestion="Check Neo4j credentials and database administration permissions.",
            ) from err

        try:
            with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
                log.info("create_constraint", database=config.database)
                session.run(
                    """
                    CREATE CONSTRAINT github_repository_key_unique IF NOT EXISTS
                    FOR (repo:GitHubRepository)
                    REQUIRE repo.key IS UNIQUE
                    """
                ).consume()
                session.run(
                    """
                    MATCH (repo:GitHubRepository)
                    REMOVE repo.owner, repo.repo, repo.url, repo.updatedAt
                    """
                ).consume()
        except Neo4jError as err:
            raise AppError(
                ErrorCode.EXTERNAL,
                "Could not initialize GitHub repository registry",
                context={"database": config.database, "error": str(err)},
                suggestion="Run registry initialization after the target database is online.",
            ) from err
    finally:
        driver.close()

    return RegistryResult(database=config.database, count=0)


def write_upsert_repository(
    config: Neo4jConfig, record: RepositoryRecord
) -> RepositoryRecord:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    now = datetime.now(UTC).isoformat()
    try:
        with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
            log.info("upsert_repository", database=config.database, key=record.key)
            session.run(
                """
                MERGE (repo:GitHubRepository {key: $key})
                ON CREATE SET repo.createdAt = $now
                REMOVE repo.owner, repo.repo, repo.url, repo.updatedAt
                """,
                key=record.key,
                now=now,
            ).consume()
    except Neo4jError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not upsert GitHub repository registry record",
            context={"database": config.database, "key": record.key, "error": str(err)},
            suggestion="Run `just registry-init` and retry.",
        ) from err
    finally:
        driver.close()

    return record


def write_remove_repository(config: Neo4jConfig, key: str) -> RegistryResult:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    try:
        with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
            log.info("remove_repository", database=config.database, key=key)
            result = session.run(
                """
                MATCH (repo:GitHubRepository {key: $key})
                DELETE repo
                RETURN count(repo) AS count
                """,
                key=key,
            )
            record = result.single()
            count = 0 if record is None else record["count"]
    except Neo4jError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not remove GitHub repository registry record",
            context={"database": config.database, "key": key, "error": str(err)},
            suggestion="Run `just registry-init` and retry.",
        ) from err
    finally:
        driver.close()

    return RegistryResult(database=config.database, count=count)


def list_repositories(config: Neo4jConfig) -> list[RepositoryRecord]:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    try:
        with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
            result = session.run(
                """
                MATCH (repo:GitHubRepository)
                RETURN repo.key AS key, repo.createdAt AS createdAt
                ORDER BY repo.key
                """
            )
            records = [
                RepositoryRecord(
                    key=record["key"],
                    createdAt=record["createdAt"],
                )
                for record in result
            ]
    except Neo4jError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not list GitHub repository registry records",
            context={"database": config.database, "error": str(err)},
            suggestion="Run `just registry-init` and retry.",
        ) from err
    finally:
        driver.close()

    return records


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))


def _print_error(err: AppError, *, as_json: bool) -> NoReturn:
    if as_json:
        print(
            json.dumps(err.to_dict(), ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
    else:
        print(f"[{err.code}] {err.message}", file=sys.stderr)
        if err.suggestion is not None:
            print(f"suggestion: {err.suggestion}", file=sys.stderr)
    sys.exit(1)


def _cmd_init(*, as_json: bool) -> int:
    try:
        config = _load_neo4j_config()
        result = write_initialize_registry(config)
    except AppError as err:
        _print_error(err, as_json=as_json)

    if as_json:
        _print_json(result.model_dump())
    else:
        print(f"initialized registry database: {result.database}")
    return 0


def _cmd_upsert(key: str, *, as_json: bool) -> int:
    try:
        config = _load_neo4j_config()
        record = write_upsert_repository(config, _repository_record_from_key(key))
    except AppError as err:
        _print_error(err, as_json=as_json)

    if as_json:
        _print_json(record.model_dump())
    else:
        print(record.key)
    return 0


def _cmd_remove(key: str, *, as_json: bool) -> int:
    try:
        config = _load_neo4j_config()
        result = write_remove_repository(config, _repository_record_from_key(key).key)
    except AppError as err:
        _print_error(err, as_json=as_json)

    if as_json:
        _print_json(result.model_dump())
    else:
        print(f"removed: {key}")
    return 0


def _cmd_list(*, as_json: bool) -> int:
    try:
        config = _load_neo4j_config()
        records = list_repositories(config)
    except AppError as err:
        _print_error(err, as_json=as_json)

    if as_json:
        _print_json([record.model_dump() for record in records])
    else:
        for record in records:
            print(record.key)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub study repository registry")
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Output results as JSON"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the registry database and constraints")

    upsert_parser = subparsers.add_parser(
        "upsert", help="Create or update a repository registry record"
    )
    upsert_parser.add_argument("key", help="GitHub repository key as owner/repo")

    remove_parser = subparsers.add_parser(
        "remove", help="Remove a repository registry record"
    )
    remove_parser.add_argument("key", help="GitHub repository key as owner/repo")

    subparsers.add_parser("list", help="List registered repository keys")

    args = parser.parse_args()

    match args.command:
        case "init":
            return _cmd_init(as_json=args.as_json)
        case "upsert":
            return _cmd_upsert(args.key, as_json=args.as_json)
        case "remove":
            return _cmd_remove(args.key, as_json=args.as_json)
        case "list":
            return _cmd_list(as_json=args.as_json)
        case _:
            parser.print_help(sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
