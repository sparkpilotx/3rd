# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "neo4j",
#     "pydantic",
#     "structlog",
# ]
# ///

from datetime import UTC, datetime
from typing import LiteralString, cast

import structlog
from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, ConfigDict

from github_study.core import AppError, ErrorCode, GitHubRepoKey, Neo4jConfig

__all__ = [
    "RegistryResult",
    "RepositoryRecord",
    "read_repository_records",
    "write_initialize_registry",
    "write_remove_repository",
    "write_upsert_repository",
]

log = structlog.get_logger()


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    key: str
    createdAt: str | None = None


class RegistryResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    database: str
    count: int


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
                suggestion="Verify Neo4j credentials have database administration permissions.",
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
                suggestion="Run this again after the target Neo4j database is online.",
            ) from err
    finally:
        driver.close()

    return RegistryResult(database=config.database, count=0)


def write_upsert_repository(
    config: Neo4jConfig, key: GitHubRepoKey
) -> RepositoryRecord:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    now = datetime.now(UTC).isoformat()
    try:
        with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
            log.info("upsert_repository", database=config.database, key=key.value)
            session.run(
                """
                MERGE (repo:GitHubRepository {key: $key})
                ON CREATE SET repo.createdAt = $now
                REMOVE repo.owner, repo.repo, repo.url, repo.updatedAt
                """,
                key=key.value,
                now=now,
            ).consume()
    except Neo4jError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not upsert GitHub repository registry record",
            context={"database": config.database, "key": key.value, "error": str(err)},
            suggestion="Run `just registry-init` and retry.",
        ) from err
    finally:
        driver.close()

    return RepositoryRecord(key=key.value)


def write_remove_repository(config: Neo4jConfig, key: GitHubRepoKey) -> RegistryResult:
    driver = GraphDatabase.driver(  # pyright: ignore[reportUnknownMemberType]
        config.uri,
        auth=(config.username, config.password),
    )
    try:
        with driver.session(database=config.database) as session:  # pyright: ignore[reportUnknownMemberType]
            log.info("remove_repository", database=config.database, key=key.value)
            result = session.run(
                """
                MATCH (repo:GitHubRepository {key: $key})
                DELETE repo
                RETURN count(repo) AS count
                """,
                key=key.value,
            )
            record = result.single()
            count = 0 if record is None else record["count"]
    except Neo4jError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not remove GitHub repository registry record",
            context={"database": config.database, "key": key.value, "error": str(err)},
            suggestion="Run `just registry-init` and retry.",
        ) from err
    finally:
        driver.close()

    return RegistryResult(database=config.database, count=count)


def read_repository_records(config: Neo4jConfig) -> list[RepositoryRecord]:
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
                RepositoryRecord(key=record["key"], createdAt=record["createdAt"])
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
