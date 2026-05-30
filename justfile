set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()

# Show available routines.
default:
    @just --list

# Add a GitHub repo key to the Neo4j registry.
add-key key:
    @"{{root}}/scripts/github-study-repo.sh" add "{{key}}"

# Remove a GitHub repo key from the Neo4j registry without deleting its local checkout.
remove-key key:
    @"{{root}}/scripts/github-study-repo.sh" remove "{{key}}"

# Create or update the local checkout derived from a registered key.
sync-key key:
    @"{{root}}/scripts/github-study-repo.sh" sync "{{key}}"

# Select the latest appropriate upstream ref for a registered key without changing checkout.
select-key key:
    @"{{root}}/scripts/github-study-repo.sh" select "{{key}}"

# Create the Neo4j registry database and constraints.
registry-init:
    @"{{root}}/scripts/github-study-repo.sh" registry-init

# Verify the local checkout derived from a registered key.
verify-key key:
    @"{{root}}/scripts/github-study-repo.sh" verify "{{key}}"

# Create or update every local checkout derived from Neo4j registry keys.
sync-all:
    @"{{root}}/scripts/github-study-repo.sh" sync-all

# List registered keys and derived local checkout state.
list:
    @"{{root}}/scripts/github-study-repo.sh" list

# Audit managed checkout consistency and unmanaged local checkouts.
audit:
    @"{{root}}/scripts/github-study-repo.sh" audit
