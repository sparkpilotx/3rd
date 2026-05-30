set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()

# Show available routines.
default:
    @just --list

# Register a GitHub repo key in the Neo4j collection.
register-key key:
    @"{{root}}/scripts/github-study-repo.sh" register "{{key}}"

# Remove a GitHub repo key from the Neo4j collection without deleting its checkout.
unregister-key key:
    @"{{root}}/scripts/github-study-repo.sh" unregister "{{key}}"

# Create a read-only local checkout for a GitHub repo key if it is missing.
checkout-key key:
    @"{{root}}/scripts/github-study-repo.sh" checkout "{{key}}"

# Update an existing checkout from owner/repo or a local path.
update-key key:
    @"{{root}}/scripts/github-study-repo.sh" update "{{key}}"

# Re-select the latest appropriate upstream ref without changing a checkout.
select-key key:
    @"{{root}}/scripts/github-study-repo.sh" select "{{key}}"

# Create the Neo4j registry database and constraints.
registry-init:
    @"{{root}}/scripts/github-study-repo.sh" registry-init

# List repository keys from the Neo4j registry.
registry-list:
    @"{{root}}/scripts/github-study-repo.sh" registry-list

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
