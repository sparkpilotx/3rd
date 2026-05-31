# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "neo4j",
#     "pydantic",
#     "structlog",
# ]
# ///

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from enum import StrEnum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

from github_study.core import (
    AppError,
    CheckoutConfig,
    ErrorCode,
    GitHubRepoKey,
    print_error,
    print_json,
    read_checkout_config_from_env,
    read_neo4j_config_from_env,
)
from github_study.registry_store import read_repository_records

__all__ = ["main"]

structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

_PRE_RELEASE_PARTS = (
    "alpha",
    "beta",
    "rc",
    "preview",
    "pre",
    "snapshot",
    "nightly",
    "dev",
    "canary",
)


class CheckoutStatus(StrEnum):
    BLOCKED = "blocked"
    MISSING = "missing"
    PRESENT = "present"


class SelectedRefKind(StrEnum):
    BRANCH = "branch"
    TAG = "tag"


class SelectedRef(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    kind: SelectedRefKind
    name: str
    reason: str


class CommitInfo(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    hash: str
    date: str
    subject: str
    author: str


class RemoteUrlSet(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    fetch: str
    push: str


class CheckoutRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    key: str
    path: str
    selectedRef: str
    commitHash: str
    latestCommit: CommitInfo
    remoteUrls: RemoteUrlSet


class CheckoutListItem(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    key: str
    status: CheckoutStatus
    path: str


class AuditIssue(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    kind: str
    key: str
    path: str
    detail: str


class AuditResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    ok: bool
    issues: list[AuditIssue]


def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _write_human(message: str) -> None:
    print(message, file=sys.stderr)


def _run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Missing required command",
            context={"command": argv[0]},
            suggestion=f"Install `{argv[0]}` and retry.",
        ) from err
    except OSError as err:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not run external command",
            context={
                "command": argv,
                "cwd": str(cwd) if cwd else None,
                "error": str(err),
            },
        ) from err

    if check and completed.returncode != 0:
        raise AppError(
            ErrorCode.EXTERNAL,
            "External command failed",
            context={
                "command": argv,
                "cwd": str(cwd) if cwd else None,
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            },
        )

    return completed


def _require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Missing required command",
            context={"command": name},
            suggestion=f"Install `{name}` and retry.",
        )


def _registered_keys() -> list[GitHubRepoKey]:
    records = read_repository_records(read_neo4j_config_from_env())
    return [GitHubRepoKey.parse(record.key) for record in records]


def _registered_key_set() -> set[str]:
    return {key.value for key in _registered_keys()}


def _require_registered_key(raw_key: str) -> GitHubRepoKey:
    key = GitHubRepoKey.parse(raw_key)
    if key.value not in _registered_key_set():
        raise AppError(
            ErrorCode.NOT_FOUND,
            "Repository is not registered in Neo4j",
            context={"key": key.value},
            suggestion=f"just registry-add {key.value}",
        )
    return key


def _path_for_key(config: CheckoutConfig, key: GitHubRepoKey) -> Path:
    return config.root / key.owner / key.repo


def _is_prerelease_name(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _PRE_RELEASE_PARTS)


def _is_github_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return (
        "rate limit" in lowered
        or "rate-limit" in lowered
        or "abuse detection" in lowered
    )


def _github_rate_limit_reset_epoch() -> int | None:
    completed = _run_command(
        [
            "gh",
            "api",
            "rate_limit",
            "--jq",
            "[.resources.core.reset, .resources.graphql.reset, .rate.reset] | map(select(. != null)) | max",
        ],
        check=False,
    )
    if completed.returncode != 0:
        return None

    raw = completed.stdout.strip()
    if raw.isdecimal():
        return int(raw)
    return None


def _wait_for_github_rate_limit(key: GitHubRepoKey) -> None:
    reset_epoch = _github_rate_limit_reset_epoch()
    now_epoch = int(time.time())
    wait_seconds = 60 if reset_epoch is None else max(reset_epoch - now_epoch + 5, 1)
    _write_human(
        f"GitHub API rate limit while listing releases for {key.value}; "
        f"waiting {wait_seconds}s before retrying"
    )
    time.sleep(wait_seconds)


def _latest_release_tag(key: GitHubRepoKey) -> str | None:
    _require_command("gh")
    while True:
        completed = _run_command(
            [
                "gh",
                "release",
                "list",
                "--repo",
                key.value,
                "--limit",
                "100",
                "--json",
                "tagName,isPrerelease,isDraft",
                "--jq",
                ".[] | select(.isDraft == false and .isPrerelease == false) | .tagName",
            ],
            check=False,
        )
        if completed.returncode == 0:
            for tag in completed.stdout.splitlines():
                if tag and not _is_prerelease_name(tag):
                    return tag
            return None

        error_text = completed.stderr.strip()
        if _is_github_rate_limit_error(error_text):
            _wait_for_github_rate_limit(key)
            continue

        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not list GitHub releases",
            context={
                "key": key.value,
                "returncode": completed.returncode,
                "stderr": error_text,
            },
        )


def _latest_stable_local_tag(path: Path) -> str | None:
    completed = _run_command(["git", "tag", "--sort=-v:refname"], cwd=path)
    for tag in completed.stdout.splitlines():
        if tag and not _is_prerelease_name(tag):
            return tag
    return None


def _latest_stable_remote_tag(key: GitHubRepoKey) -> str | None:
    tmp_path = Path(tempfile.mkdtemp(prefix="github-study-tags-"))
    try:
        _run_command(["git", "init", "-q"], cwd=tmp_path)
        _run_command(["git", "remote", "add", "origin", key.clone_url], cwd=tmp_path)
        _run_command(
            ["git", "fetch", "-q", "--tags", "origin", "+refs/tags/*:refs/tags/*"],
            cwd=tmp_path,
        )
        return _latest_stable_local_tag(tmp_path)
    finally:
        shutil.rmtree(tmp_path)


def _default_branch_for_key(key: GitHubRepoKey) -> str:
    completed = _run_command(["git", "ls-remote", "--symref", key.clone_url, "HEAD"])
    for line in completed.stdout.splitlines():
        if line.startswith("ref:"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].removeprefix("refs/heads/")
    raise AppError(
        ErrorCode.EXTERNAL,
        "Could not identify default branch",
        context={"key": key.value},
    )


def _ensure_reachable(key: GitHubRepoKey) -> None:
    _run_command(["git", "ls-remote", "--exit-code", key.clone_url, "HEAD"])


def _select_ref_for_key(key: GitHubRepoKey, *, local_path: Path | None) -> SelectedRef:
    release_tag = _latest_release_tag(key)
    if release_tag is not None:
        return SelectedRef(
            kind=SelectedRefKind.TAG,
            name=release_tag,
            reason="GitHub Release",
        )

    if local_path is not None and (local_path / ".git").is_dir():
        local_tag = _latest_stable_local_tag(local_path)
        if local_tag is not None:
            return SelectedRef(
                kind=SelectedRefKind.TAG, name=local_tag, reason="git tag"
            )

    return SelectedRef(
        kind=SelectedRefKind.BRANCH,
        name=_default_branch_for_key(key),
        reason="default branch",
    )


def _select_ref_from_remote(key: GitHubRepoKey) -> SelectedRef:
    release_tag = _latest_release_tag(key)
    if release_tag is not None:
        return SelectedRef(
            kind=SelectedRefKind.TAG,
            name=release_tag,
            reason="GitHub Release",
        )

    stable_tag = _latest_stable_remote_tag(key)
    if stable_tag is not None:
        return SelectedRef(kind=SelectedRefKind.TAG, name=stable_tag, reason="git tag")

    return SelectedRef(
        kind=SelectedRefKind.BRANCH,
        name=_default_branch_for_key(key),
        reason="default branch",
    )


def _key_for_checkout_path(path: Path) -> GitHubRepoKey:
    completed = _run_command(["git", "remote", "get-url", "origin"], cwd=path)
    return GitHubRepoKey.parse(completed.stdout.strip())


def _dirty_status(path: Path) -> str:
    return _run_command(["git", "status", "--porcelain"], cwd=path).stdout.strip()


def _stop_if_dirty(path: Path) -> None:
    dirty = _dirty_status(path)
    if dirty:
        raise AppError(
            ErrorCode.UNSAFE_STATE,
            "Local checkout has modifications; refusing to update",
            context={"path": str(path), "status": dirty},
            suggestion="Commit, stash, or remove local changes before syncing.",
        )


def _checkout_selected_ref(path: Path, selected: SelectedRef) -> None:
    match selected.kind:
        case SelectedRefKind.TAG:
            _run_command(["git", "switch", "--detach", selected.name], cwd=path)
        case SelectedRefKind.BRANCH:
            branch_exists = _run_command(
                [
                    "git",
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{selected.name}",
                ],
                cwd=path,
                check=False,
            )
            if branch_exists.returncode == 0:
                _run_command(["git", "switch", selected.name], cwd=path)
            else:
                _run_command(
                    [
                        "git",
                        "switch",
                        "--create",
                        selected.name,
                        "--track",
                        f"origin/{selected.name}",
                    ],
                    cwd=path,
                )
            _run_command(
                ["git", "pull", "--ff-only", "origin", selected.name], cwd=path
            )


def _write_disable_push(path: Path) -> None:
    _run_command(["git", "remote", "set-url", "--push", "origin", "DISABLED"], cwd=path)


def _current_ref_label(path: Path) -> str:
    exact_tag = _run_command(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        cwd=path,
        check=False,
    )
    if exact_tag.returncode == 0 and exact_tag.stdout.strip():
        return f"tag {exact_tag.stdout.strip()}"

    branch = _run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path
    ).stdout.strip()
    if branch == "HEAD":
        return "detached HEAD"
    return f"branch {branch}"


def _latest_commit(path: Path) -> CommitInfo:
    completed = _run_command(
        [
            "git",
            "log",
            "-1",
            "--date=iso",
            "--pretty=format:%H%x1f%ad%x1f%s%x1f%an <%ae>",
        ],
        cwd=path,
    )
    fields = completed.stdout.split("\x1f")
    if len(fields) != 4:
        raise AppError(
            ErrorCode.EXTERNAL,
            "Could not parse latest commit",
            context={"path": str(path), "output": completed.stdout},
        )
    return CommitInfo(
        hash=fields[0], date=fields[1], subject=fields[2], author=fields[3]
    )


def _remote_urls(path: Path) -> RemoteUrlSet:
    fetch = _run_command(
        ["git", "remote", "get-url", "origin"], cwd=path
    ).stdout.strip()
    push = _run_command(
        ["git", "remote", "get-url", "--push", "origin"],
        cwd=path,
    ).stdout.strip()
    return RemoteUrlSet(fetch=fetch, push=push)


def _verify_checkout(
    path: Path, *, expected_key: GitHubRepoKey | None
) -> CheckoutRecord:
    if not (path / ".git").is_dir():
        raise AppError(
            ErrorCode.NOT_FOUND,
            "Path is not a git checkout",
            context={"path": str(path)},
        )

    actual_key = _key_for_checkout_path(path)
    if expected_key is not None and actual_key.value != expected_key.value:
        raise AppError(
            ErrorCode.UNSAFE_STATE,
            "Checkout remote does not match registered key",
            context={
                "path": str(path),
                "expected": expected_key.value,
                "actual": actual_key.value,
            },
        )

    commit_hash = _run_command(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
    return CheckoutRecord(
        key=actual_key.value,
        path=str(path),
        selectedRef=_current_ref_label(path),
        commitHash=commit_hash,
        latestCommit=_latest_commit(path),
        remoteUrls=_remote_urls(path),
    )


def _write_sync_checkout(config: CheckoutConfig, key: GitHubRepoKey) -> CheckoutRecord:
    path = _path_for_key(config, key)
    if path.exists() and not (path / ".git").is_dir():
        raise AppError(
            ErrorCode.UNSAFE_STATE,
            "Checkout target exists but is not a git checkout",
            context={"key": key.value, "path": str(path)},
        )

    if (path / ".git").is_dir():
        actual_key = _key_for_checkout_path(path)
        if actual_key.value != key.value:
            raise AppError(
                ErrorCode.UNSAFE_STATE,
                "Checkout remote does not match registered key",
                context={
                    "path": str(path),
                    "expected": key.value,
                    "actual": actual_key.value,
                },
            )
        _stop_if_dirty(path)
        _run_command(["git", "fetch", "--prune", "--tags", "origin"], cwd=path)
    else:
        _ensure_reachable(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        _run_command(["git", "clone", key.clone_url, str(path)])
        _run_command(["git", "fetch", "--prune", "--tags", "origin"], cwd=path)

    selected = _select_ref_for_key(key, local_path=path)
    _checkout_selected_ref(path, selected)
    _write_disable_push(path)
    record = _verify_checkout(path, expected_key=key)
    _write_human(f"selected because: {selected.reason}")
    return record


def _checkout_list_items(config: CheckoutConfig) -> list[CheckoutListItem]:
    items: list[CheckoutListItem] = []
    for key in _registered_keys():
        path = _path_for_key(config, key)
        if (path / ".git").is_dir():
            status = CheckoutStatus.PRESENT
        elif path.exists():
            status = CheckoutStatus.BLOCKED
        else:
            status = CheckoutStatus.MISSING
        items.append(CheckoutListItem(key=key.value, status=status, path=str(path)))
    return items


def _audit_checkout_state(config: CheckoutConfig) -> AuditResult:
    issues: list[AuditIssue] = []
    registered_values = _registered_key_set()
    for key in _registered_keys():
        path = _path_for_key(config, key)
        if not (path / ".git").is_dir():
            issues.append(
                AuditIssue(
                    kind="missing_checkout",
                    key=key.value,
                    path=str(path),
                    detail="No git checkout exists at the derived path.",
                )
            )
            continue

        actual_key = _key_for_checkout_path(path)
        if actual_key.value != key.value:
            issues.append(
                AuditIssue(
                    kind="repo_key_mismatch",
                    key=key.value,
                    path=str(path),
                    detail=f"origin points to {actual_key.value}",
                )
            )

        push_url = _remote_urls(path).push
        if push_url != "DISABLED":
            issues.append(
                AuditIssue(
                    kind="push_enabled",
                    key=key.value,
                    path=str(path),
                    detail=push_url,
                )
            )

        dirty = _dirty_status(path)
        if dirty:
            issues.append(
                AuditIssue(
                    kind="dirty_worktree",
                    key=key.value,
                    path=str(path),
                    detail=dirty,
                )
            )

    if config.root.is_dir():
        for git_dir in config.root.glob("*/*/.git"):
            path = git_dir.parent
            actual_key = _key_for_checkout_path(path)
            if actual_key.value not in registered_values:
                issues.append(
                    AuditIssue(
                        kind="unmanaged_checkout",
                        key=actual_key.value,
                        path=str(path),
                        detail="Checkout exists locally but is not registered in Neo4j.",
                    )
                )

    return AuditResult(ok=len(issues) == 0, issues=issues)


def _cmd_select(raw_key: str, *, as_json: bool) -> int:
    try:
        key = _require_registered_key(raw_key)
        _ensure_reachable(key)
        selected = _select_ref_from_remote(key)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(selected.model_dump())
    else:
        _write_human(f"{selected.kind.value} {selected.name} {selected.reason}")
    return 0


def _cmd_sync(raw_key: str, *, as_json: bool) -> int:
    try:
        config = read_checkout_config_from_env(_workspace_root())
        record = _write_sync_checkout(config, _require_registered_key(raw_key))
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(record.model_dump())
    else:
        _print_checkout_record(record)
    return 0


def _cmd_sync_all(*, as_json: bool) -> int:
    records: list[CheckoutRecord] = []
    try:
        config = read_checkout_config_from_env(_workspace_root())
        keys = _registered_keys()
        for key in keys:
            _write_human(f"==> syncing {key.value}")
            record = _write_sync_checkout(config, key)
            records.append(record)
            if not as_json:
                _print_checkout_record(record)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json([record.model_dump() for record in records])
    return 0


def _cmd_verify(raw_key: str, *, as_json: bool) -> int:
    try:
        config = read_checkout_config_from_env(_workspace_root())
        key = _require_registered_key(raw_key)
        record = _verify_checkout(_path_for_key(config, key), expected_key=key)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(record.model_dump())
    else:
        _print_checkout_record(record)
    return 0


def _cmd_list(*, as_json: bool) -> int:
    try:
        config = read_checkout_config_from_env(_workspace_root())
        items = _checkout_list_items(config)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json([item.model_dump() for item in items])
    else:
        for item in items:
            _write_human(f"{item.key}\t{item.status.value}\t{item.path}")
    return 0


def _cmd_audit(*, as_json: bool) -> int:
    try:
        config = read_checkout_config_from_env(_workspace_root())
        result = _audit_checkout_state(config)
    except AppError as err:
        print_error(err, as_json=as_json)

    if as_json:
        print_json(result.model_dump())
    elif result.ok:
        _write_human("ok")
    else:
        for issue in result.issues:
            _write_human(f"{issue.kind}: {issue.key} ({issue.path}) {issue.detail}")
    return 0 if result.ok else 1


def _print_checkout_record(record: CheckoutRecord) -> None:
    _write_human(f"local path: {record.path}")
    _write_human(f"repo key: {record.key}")
    _write_human(f"selected ref: {record.selectedRef}")
    _write_human(f"commit hash: {record.commitHash}")
    _write_human("latest commit:")
    _write_human(f"  hash: {record.latestCommit.hash}")
    _write_human(f"  date: {record.latestCommit.date}")
    _write_human(f"  subject: {record.latestCommit.subject}")
    _write_human(f"  author: {record.latestCommit.author}")
    _write_human("remote URLs:")
    _write_human(f"  origin\t{record.remoteUrls.fetch} (fetch)")
    _write_human(f"  origin\t{record.remoteUrls.push} (push)")


def main() -> int:
    _require_command("git")

    parser = argparse.ArgumentParser(
        description="Materialize local GitHub checkouts from the Neo4j registry"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Output results as JSON"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync", help="Create or update one checkout derived from Neo4j"
    )
    sync_parser.add_argument("key", help="GitHub repository key as owner/repo")

    verify_parser = subparsers.add_parser(
        "verify", help="Verify one checkout derived from Neo4j"
    )
    verify_parser.add_argument("key", help="GitHub repository key as owner/repo")

    select_parser = subparsers.add_parser(
        "select",
        help="Select the upstream ref for a registered key without checkout changes",
    )
    select_parser.add_argument("key", help="GitHub repository key as owner/repo")

    subparsers.add_parser("sync-all", help="Create or update every derived checkout")
    subparsers.add_parser("list", help="List derived checkout state")
    subparsers.add_parser("audit", help="Audit derived checkout consistency")

    args = parser.parse_args()

    match args.command:
        case "sync":
            return _cmd_sync(args.key, as_json=args.as_json)
        case "verify":
            return _cmd_verify(args.key, as_json=args.as_json)
        case "select":
            return _cmd_select(args.key, as_json=args.as_json)
        case "sync-all":
            return _cmd_sync_all(as_json=args.as_json)
        case "list":
            return _cmd_list(as_json=args.as_json)
        case "audit":
            return _cmd_audit(as_json=args.as_json)
        case _:
            parser.print_help(sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
