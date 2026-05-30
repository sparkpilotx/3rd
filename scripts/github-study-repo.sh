#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="github.com"
REGISTRY_SCRIPT="$ROOT/scripts/github-study-registry.py"
if [ -n "${GITHUB_STUDY_CHECKOUT_ROOT:-}" ]; then
  case "$GITHUB_STUDY_CHECKOUT_ROOT" in
    /*) CHECKOUT_ROOT="$GITHUB_STUDY_CHECKOUT_ROOT" ;;
    *) CHECKOUT_ROOT="$ROOT/$GITHUB_STUDY_CHECKOUT_ROOT" ;;
  esac
else
  CHECKOUT_ROOT="$ROOT/$HOST"
fi
PRE_RELEASE_PATTERN='(alpha|beta|rc|preview|pre|snapshot|nightly|dev|canary)'

usage() {
  cat <<'USAGE'
Usage:
  github-study-repo.sh add OWNER/REPO
  github-study-repo.sh update OWNER/REPO|PATH
  github-study-repo.sh verify OWNER/REPO|PATH
  github-study-repo.sh select OWNER/REPO
  github-study-repo.sh registry-init
  github-study-repo.sh registry-import-existing
  github-study-repo.sh registry-list
  github-study-repo.sh update-all
  github-study-repo.sh list
  github-study-repo.sh audit
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

is_github_rate_limit_error() {
  printf '%s\n' "$1" | grep -Eiq 'rate limit|rate-limit|abuse detection'
}

github_rate_limit_reset_epoch() {
  gh api rate_limit --jq '[.resources.core.reset, .resources.graphql.reset, .rate.reset] | map(select(. != null)) | max' 2>/dev/null || true
}

wait_for_github_rate_limit() {
  local key="$1"
  local reset_epoch now_epoch wait_seconds

  reset_epoch="$(github_rate_limit_reset_epoch)"
  now_epoch="$(date +%s)"

  case "$reset_epoch" in
    ''|*[!0-9]*)
      wait_seconds=60
      ;;
    *)
      wait_seconds=$((reset_epoch - now_epoch + 5))
      if [ "$wait_seconds" -lt 1 ]; then
        wait_seconds=1
      fi
      ;;
  esac

  warn "GitHub API rate limit while listing releases for $key; waiting ${wait_seconds}s before retrying"
  sleep "$wait_seconds"
}

normalize_key() {
  local input="${1%/}"
  input="${input#https://github.com/}"
  input="${input#http://github.com/}"
  input="${input#git@github.com:}"
  input="${input#github.com/}"
  input="${input%.git}"

  case "$input" in
    */*) ;;
    *) die "expected GitHub repo as owner/repo, got: $1" ;;
  esac

  local owner="${input%%/*}"
  local repo="${input#*/}"
  case "$owner" in ''|*/*) die "invalid owner in repo key: $1" ;; esac
  case "$repo" in ''|*/*) die "invalid repo in repo key: $1" ;; esac

  printf '%s/%s\n' "$owner" "$repo"
}

path_for_key() {
  local key
  key="$(normalize_key "$1")"
  printf '%s/%s\n' "$CHECKOUT_ROOT" "$key"
}

url_for_key() {
  local key
  key="$(normalize_key "$1")"
  printf 'https://github.com/%s.git\n' "$key"
}

registry_command() {
  require_cmd uv
  NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "$REGISTRY_SCRIPT" "$@"
}

registry_init() {
  registry_command init
}

registry_upsert_key() {
  local key
  key="$(normalize_key "$1")"
  registry_command --json upsert "$key" >/dev/null
}

registered_repo_keys() {
  registry_command list
}

registry_require_ready() {
  registered_repo_keys >/dev/null
}

key_for_repo_path() {
  local path="$1"
  local fetch_url
  fetch_url="$(git -C "$path" remote get-url origin)"
  normalize_key "$fetch_url"
}

repo_path_from_arg() {
  local input="$1"
  if [ -d "$input/.git" ]; then
    cd "$input" && pwd
    return
  fi
  if [ -d "$ROOT/$input/.git" ]; then
    cd "$ROOT/$input" && pwd
    return
  fi
  if [ -d "$CHECKOUT_ROOT/$input/.git" ]; then
    cd "$CHECKOUT_ROOT/$input" && pwd
    return
  fi
  path_for_key "$input"
}

default_branch_for_key() {
  local key="$1"
  local url
  url="$(url_for_key "$key")"
  git ls-remote --symref "$url" HEAD |
    awk '/^ref:/ { sub("refs/heads/", "", $2); print $2; exit }'
}

ensure_reachable() {
  local key="$1"
  local url
  url="$(url_for_key "$key")"
  git ls-remote --exit-code "$url" HEAD >/dev/null
}

latest_release_tag() {
  local key="$1"
  if ! command -v gh >/dev/null 2>&1; then
    return 0
  fi

  local err_file err_text output
  while true; do
    err_file="$(mktemp)"
    if output="$(gh release list \
      --repo "$key" \
      --limit 100 \
      --json tagName,isPrerelease,isDraft \
      --jq '.[] | select(.isDraft == false and .isPrerelease == false) | .tagName' 2>"$err_file")"; then
      rm -f "$err_file"
      break
    fi

    err_text="$(cat "$err_file")"
    rm -f "$err_file"
    if is_github_rate_limit_error "$err_text"; then
      wait_for_github_rate_limit "$key"
      continue
    fi

    die "could not list GitHub releases for $key: $err_text"
  done

  printf '%s\n' "$output" |
    grep -Evi "$PRE_RELEASE_PATTERN" |
    head -n 1 || true
}

latest_stable_local_tag() {
  local path="$1"
  git -C "$path" tag --sort=-v:refname |
    grep -Evi "$PRE_RELEASE_PATTERN" |
    head -n 1
}

latest_stable_remote_tag() {
  local key="$1"
  local url
  url="$(url_for_key "$key")"
  local tmp
  tmp="$(mktemp -d)"
  local tag

  git -C "$tmp" init -q
  git -C "$tmp" remote add origin "$url"
  git -C "$tmp" fetch -q --tags origin '+refs/tags/*:refs/tags/*'
  tag="$(latest_stable_local_tag "$tmp" || true)"
  rm -rf "$tmp"
  printf '%s\n' "$tag"
}

select_ref_for_repo() {
  local key="$1"
  local path="${2:-}"
  local tag=""
  local branch=""

  tag="$(latest_release_tag "$key" || true)"
  if [ -n "$tag" ]; then
    printf 'tag %s GitHub Release\n' "$tag"
    return
  fi

  if [ -n "$path" ] && [ -d "$path/.git" ]; then
    tag="$(latest_stable_local_tag "$path" || true)"
  fi

  if [ -n "$tag" ]; then
    printf 'tag %s git tag\n' "$tag"
    return
  fi

  branch="$(default_branch_for_key "$key")"
  [ -n "$branch" ] || die "could not identify default branch for $key"
  printf 'branch %s default branch\n' "$branch"
}

stop_if_dirty() {
  local path="$1"
  local dirty
  dirty="$(git -C "$path" status --porcelain)"
  if [ -n "$dirty" ]; then
    printf 'Local modifications found in %s; refusing to update.\n' "$path" >&2
    printf '%s\n' "$dirty" >&2
    exit 1
  fi
}

checkout_selected_ref() {
  local path="$1"
  local kind="$2"
  local ref="$3"

  case "$kind" in
    tag)
      git -C "$path" checkout --detach "$ref"
      ;;
    branch)
      if git -C "$path" show-ref --verify --quiet "refs/heads/$ref"; then
        git -C "$path" checkout "$ref"
      else
        git -C "$path" checkout -b "$ref" --track "origin/$ref"
      fi
      git -C "$path" pull --ff-only origin "$ref"
      ;;
    *)
      die "unknown ref kind: $kind"
      ;;
  esac
}

disable_push() {
  local path="$1"
  git -C "$path" remote set-url --push origin DISABLED
}

current_ref_label() {
  local path="$1"
  local exact_tag
  exact_tag="$(git -C "$path" describe --tags --exact-match HEAD 2>/dev/null || true)"
  if [ -n "$exact_tag" ]; then
    printf 'tag %s\n' "$exact_tag"
    return
  fi

  local branch
  branch="$(git -C "$path" rev-parse --abbrev-ref HEAD)"
  if [ "$branch" = "HEAD" ]; then
    printf 'detached HEAD\n'
  else
    printf 'branch %s\n' "$branch"
  fi
}

verify_repo() {
  local path="$1"
  [ -d "$path/.git" ] || die "not a git checkout: $path"

  local key
  key="$(key_for_repo_path "$path")"

  printf 'local path: %s\n' "$path"
  printf 'repo key: %s\n' "$key"
  printf 'selected ref: %s\n' "$(current_ref_label "$path")"
  printf 'commit hash: %s\n' "$(git -C "$path" rev-parse HEAD)"
  printf 'latest commit:\n'
  git -C "$path" log -1 --date=iso --pretty=format:'  hash: %H%n  date: %ad%n  subject: %s%n  author: %an <%ae>%n'
  printf 'remote URLs:\n'
  git -C "$path" remote -v | sed 's/^/  /'
}

add_repo() {
  local key
  key="$(normalize_key "$1")"
  registry_require_ready

  local path
  path="$(path_for_key "$key")"
  local url
  url="$(url_for_key "$key")"

  if [ -e "$path" ] && [ ! -d "$path/.git" ]; then
    die "target exists but is not a git checkout: $path"
  fi

  if [ -d "$path/.git" ]; then
    update_repo "$path"
    registry_upsert_key "$key"
    return
  fi

  ensure_reachable "$key"
  mkdir -p "$(dirname "$path")"
  git clone "$url" "$path"
  git -C "$path" fetch --prune --tags origin

  local selected kind ref reason
  selected="$(select_ref_for_repo "$key" "$path")"
  kind="${selected%% *}"
  ref="${selected#* }"
  reason="${ref#* }"
  ref="${ref%% *}"

  checkout_selected_ref "$path" "$kind" "$ref"
  disable_push "$path"
  printf 'selected because: %s\n' "$reason"
  verify_repo "$path"
  registry_upsert_key "$key"
}

update_repo() {
  local path
  path="$(repo_path_from_arg "$1")"
  [ -d "$path/.git" ] || die "not a git checkout: $path"

  stop_if_dirty "$path"
  local key
  key="$(key_for_repo_path "$path")"
  git -C "$path" fetch --prune --tags origin

  local selected kind ref reason
  selected="$(select_ref_for_repo "$key" "$path")"
  kind="${selected%% *}"
  ref="${selected#* }"
  reason="${ref#* }"
  ref="${ref%% *}"

  checkout_selected_ref "$path" "$kind" "$ref"
  disable_push "$path"
  printf 'selected because: %s\n' "$reason"
  verify_repo "$path"
}

select_repo() {
  local key
  key="$(normalize_key "$1")"
  ensure_reachable "$key"

  local path
  path="$(path_for_key "$key")"
  if [ -d "$path/.git" ]; then
    git -C "$path" fetch --prune --tags origin
    select_ref_for_repo "$key" "$path"
  else
    local tag branch
    tag="$(latest_release_tag "$key" || true)"
    if [ -n "$tag" ]; then
      printf 'tag %s GitHub Release\n' "$tag"
      return
    fi
    tag="$(latest_stable_remote_tag "$key" || true)"
    if [ -n "$tag" ]; then
      printf 'tag %s git tag\n' "$tag"
      return
    fi
    branch="$(default_branch_for_key "$key")"
    printf 'branch %s default branch\n' "$branch"
  fi
}

filesystem_repo_paths() {
  if [ ! -d "$CHECKOUT_ROOT" ]; then
    return
  fi
  local owner_dir repo_dir
  for owner_dir in "$CHECKOUT_ROOT"/*; do
    [ -d "$owner_dir" ] || continue
    for repo_dir in "$owner_dir"/*; do
      [ -d "$repo_dir/.git" ] || continue
      printf '%s\n' "$repo_dir"
    done
  done | sort
}

list_repos() {
  registered_repo_keys | while IFS= read -r key; do
    [ -n "$key" ] || continue
    printf '%s\t%s\n' "$key" "$(path_for_key "$key")"
  done
}

update_all() {
  registered_repo_keys | while IFS= read -r key; do
    [ -n "$key" ] || continue
    printf '==> updating %s\n' "$key"
    update_repo "$(path_for_key "$key")"
  done
}

audit_repos() {
  local failed=0
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    local path push_url dirty actual_key
    path="$(path_for_key "$key")"
    if [ ! -d "$path/.git" ]; then
      printf 'missing checkout: %s (%s)\n' "$key" "$path"
      failed=1
      continue
    fi

    actual_key="$(key_for_repo_path "$path")"
    push_url="$(git -C "$path" remote get-url --push origin 2>/dev/null || true)"
    dirty="$(git -C "$path" status --porcelain)"

    if [ "$actual_key" != "$key" ]; then
      printf 'repo key mismatch: %s (%s has remote %s)\n' "$key" "$path" "$actual_key"
      failed=1
    fi
    if [ "$push_url" != "DISABLED" ]; then
      printf 'push enabled: %s (%s)\n' "$key" "$push_url"
      failed=1
    fi
    if [ -n "$dirty" ]; then
      printf 'dirty worktree: %s\n%s\n' "$key" "$dirty"
      failed=1
    fi
    if [ "$push_url" = "DISABLED" ] && [ -z "$dirty" ]; then
      printf 'ok: %s\n' "$key"
    fi
  done < <(registered_repo_keys)
  return "$failed"
}

registry_import_existing() {
  filesystem_repo_paths | while IFS= read -r path; do
    [ -n "$path" ] || continue
    local key
    key="$(key_for_repo_path "$path")"
    registry_upsert_key "$key"
    printf 'imported: %s\t%s\n' "$key" "$path"
  done
}

main() {
  require_cmd git

  local command="${1:-}"
  shift || true

  case "$command" in
    add)
      [ "$#" -eq 1 ] || die "add expects OWNER/REPO"
      add_repo "$1"
      ;;
    update)
      [ "$#" -eq 1 ] || die "update expects OWNER/REPO or PATH"
      update_repo "$1"
      ;;
    verify)
      [ "$#" -eq 1 ] || die "verify expects OWNER/REPO or PATH"
      verify_repo "$(repo_path_from_arg "$1")"
      ;;
    select)
      [ "$#" -eq 1 ] || die "select expects OWNER/REPO"
      select_repo "$1"
      ;;
    registry-init)
      [ "$#" -eq 0 ] || die "registry-init expects no arguments"
      registry_init
      ;;
    registry-import-existing)
      [ "$#" -eq 0 ] || die "registry-import-existing expects no arguments"
      registry_import_existing
      ;;
    registry-list)
      [ "$#" -eq 0 ] || die "registry-list expects no arguments"
      registered_repo_keys
      ;;
    update-all)
      [ "$#" -eq 0 ] || die "update-all expects no arguments"
      update_all
      ;;
    list)
      [ "$#" -eq 0 ] || die "list expects no arguments"
      list_repos
      ;;
    audit)
      [ "$#" -eq 0 ] || die "audit expects no arguments"
      audit_repos
      ;;
    -h|--help|help|'')
      usage
      ;;
    *)
      usage >&2
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
