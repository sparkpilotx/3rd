## Workspace Purpose
This workspace stores read-only local study checkouts of GitHub repositories for learning and inspection.

Treat cloned repositories as upstream-owned source material. Do not push, commit, open PRs, or edit upstream repository files unless explicitly asked.

## Layout
Use `owner/repo` as the canonical repository key.

Store GitHub checkouts under:

```text
github.com/<owner>/<repo>
```

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
