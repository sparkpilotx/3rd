# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "pydantic",
# ]
# ///

import json
import os
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AppError",
    "CheckoutConfig",
    "ErrorCode",
    "GitHubRepoKey",
    "Neo4jConfig",
    "print_error",
    "print_json",
    "read_checkout_config_from_env",
    "read_neo4j_config_from_env",
]

_DEFAULT_DATABASE = "workspace-3rd"
_DATABASE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_KEY_PATTERN = re.compile(r"^[^/\s]+/[^/\s]+$")
_GITHUB_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "git@github.com:",
    "github.com/",
)


class ErrorCode(StrEnum):
    EXTERNAL = "EXTERNAL"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_ENV = "MISSING_ENV"
    NOT_FOUND = "NOT_FOUND"
    UNSAFE_STATE = "UNSAFE_STATE"


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


class GitHubRepoKey(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    value: str

    @property
    def owner(self) -> str:
        return self.value.split("/", maxsplit=1)[0]

    @property
    def repo(self) -> str:
        return self.value.split("/", maxsplit=1)[1]

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.value}.git"

    @classmethod
    def parse(cls, raw: str) -> "GitHubRepoKey":
        normalized = raw.strip().rstrip("/")
        for prefix in _GITHUB_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized.removeprefix(prefix)
                break
        normalized = normalized.removesuffix(".git")

        if not _KEY_PATTERN.fullmatch(normalized):
            raise AppError(
                ErrorCode.INVALID_INPUT,
                "Invalid GitHub repository key",
                context={"input": raw},
                suggestion="Pass a GitHub repository as owner/repo.",
            )

        owner, repo = normalized.split("/", maxsplit=1)
        if "/" in owner or "/" in repo:
            raise AppError(
                ErrorCode.INVALID_INPUT,
                "Invalid GitHub repository key",
                context={"input": raw},
                suggestion="Pass a GitHub repository as owner/repo.",
            )

        return cls(value=normalized)


class Neo4jConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    uri: str
    username: str
    password: str
    database: str


class CheckoutConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    host: str
    root: Path


def read_neo4j_config_from_env() -> Neo4jConfig:
    missing = [
        name
        for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise AppError(
            ErrorCode.MISSING_ENV,
            "Missing required Neo4j environment variables",
            context={"variables": missing},
            suggestion="export " + " ".join(f"{name}=<value>" for name in missing),
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


def read_checkout_config_from_env(workspace_root: Path) -> CheckoutConfig:
    configured_root = os.environ.get("GITHUB_STUDY_CHECKOUT_ROOT")
    if configured_root is None or configured_root == "":
        root = workspace_root / "github.com"
    else:
        configured_path = Path(configured_root)
        root = (
            configured_path
            if configured_path.is_absolute()
            else workspace_root / configured_path
        )

    return CheckoutConfig(host="github.com", root=root)


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))


def print_error(err: AppError, *, as_json: bool) -> NoReturn:
    if as_json:
        print(
            json.dumps(err.to_dict(), ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
    else:
        print(f"[{err.code}] {err.message}", file=sys.stderr)
        if err.context:
            print(
                json.dumps(err.context, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
        if err.suggestion is not None:
            print(f"suggestion: {err.suggestion}", file=sys.stderr)
    sys.exit(1)
