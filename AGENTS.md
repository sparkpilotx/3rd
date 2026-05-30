## Workspace Purpose
This workspace stores read-only local study checkouts of GitHub repositories for learning and inspection.

Treat cloned repositories as upstream-owned source material. Do not push, commit, open PRs, or edit upstream repository files unless explicitly asked.

## Layout
Use `owner/repo` as the canonical repository key.

Store GitHub checkouts under this default local root:

```text
github.com/<owner>/<repo>
```

Override the local storage root with `GITHUB_STUDY_CHECKOUT_ROOT`. The remote service and accepted repo keys remain GitHub-only; this setting only changes where local checkouts are stored.

## Your Responsibilities to assist user interactive with you

- Interpret the requested repo or checkout.
- Use the project skill and `just` recipes.
- Preserve read-only upstream checkouts.
- Report exact selected refs and remote URLs.
- Ask before acting when version selection is ambiguous.

## Read-Only Rules
After every clone or update, ensure:

```bash
git remote set-url --push origin DISABLED
```

Before updating an existing checkout, stop if the worktree is dirty unless the user explicitly asks to handle local changes.

Never use destructive Git commands such as `git reset --hard`, `git clean`, or `git checkout -- <path>` unless the user explicitly approves that exact action.

## Version Policy
Prefer the latest non-prerelease GitHub Release.

Ignore prerelease-looking refs containing:

```text
alpha beta rc preview pre snapshot nightly dev canary
```

If there are no suitable GitHub Releases, use the newest stable-looking tag. If there are no stable tags, use the repository default branch from Git metadata.

If multiple stable release tracks exist, stop and explain the candidates before changing the checkout.

GitHub API rate limits are not a reason to fall back from Releases to tags or branches. When release lookup is rate-limited, wait until the GitHub rate limit reset time and retry so `add`, `update`, `select`, and `update-all` keep consistent release-first selection semantics.

If release lookup fails for a non-rate-limit GitHub API error, stop with a clear error instead of silently selecting a lower-priority ref.
