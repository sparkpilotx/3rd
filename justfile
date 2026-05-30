set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()

# Show available routines.
default:
    @just --list

# Add or update a GitHub repo as a read-only latest/stable study checkout.
add owner repo:
    @"{{root}}/scripts/github-study-repo.sh" add "{{owner}}/{{repo}}"

# Add or update a repo from owner/repo, github.com/owner/repo, or a GitHub URL.
add-key key:
    @"{{root}}/scripts/github-study-repo.sh" add "{{key}}"

# Update an existing checkout by owner and repo.
update owner repo:
    @"{{root}}/scripts/github-study-repo.sh" update "{{owner}}/{{repo}}"

# Update an existing checkout from owner/repo or a local path.
update-key key:
    @"{{root}}/scripts/github-study-repo.sh" update "{{key}}"

# Re-select the latest appropriate upstream ref without changing a checkout.
select owner repo:
    @"{{root}}/scripts/github-study-repo.sh" select "{{owner}}/{{repo}}"

# Create the Neo4j registry database and constraints.
registry-init:
    @"{{root}}/scripts/github-study-repo.sh" registry-init

# Import existing filesystem checkouts into the Neo4j registry.
registry-import-existing:
    @"{{root}}/scripts/github-study-repo.sh" registry-import-existing

# List repository keys from the Neo4j registry.
registry-list:
    @"{{root}}/scripts/github-study-repo.sh" registry-list

# Verify readonly state and current ref.
verify owner repo:
    @"{{root}}/scripts/github-study-repo.sh" verify "{{owner}}/{{repo}}"

# Verify from owner/repo or a local path.
verify-key key:
    @"{{root}}/scripts/github-study-repo.sh" verify "{{key}}"

# Update every managed checkout.
update-all:
    @"{{root}}/scripts/github-study-repo.sh" update-all

# List managed checkouts.
list:
    @"{{root}}/scripts/github-study-repo.sh" list

# Audit dirty worktrees and push remotes.
audit:
    @"{{root}}/scripts/github-study-repo.sh" audit
