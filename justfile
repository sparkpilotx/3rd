set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()

# Show available routines.
default:
    @just --list

# Create the Neo4j source-of-truth database and constraints.
registry-init:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/registry.py" init

# Add a GitHub repo key to the Neo4j source of truth.
registry-add key:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/registry.py" add "{{key}}"

# Remove a GitHub repo key from the Neo4j source of truth without deleting local files.
registry-remove key:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/registry.py" remove "{{key}}"

# List GitHub repo keys in the Neo4j source of truth.
registry-list:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/registry.py" list

# List local checkout state derived from Neo4j.
checkout-list:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" list

# Select the upstream ref for a registered key without changing local checkout.
checkout-select key:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" select "{{key}}"

# Create or update the local checkout derived from one Neo4j key.
checkout-sync key:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" sync "{{key}}"

# Verify the local checkout derived from one Neo4j key.
checkout-verify key:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" verify "{{key}}"

# Create or update every local checkout derived from Neo4j keys.
checkout-sync-all:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" sync-all

# Audit derived checkout consistency and unmanaged local checkouts.
checkout-audit:
    @NEO4J_DATABASE="${NEO4J_DATABASE:-workspace-3rd}" uv run "{{root}}/scripts/checkout.py" audit
