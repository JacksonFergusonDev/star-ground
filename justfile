set shell := ["bash", "-euc", "-o", "pipefail"]
set unstable
set quiet

# --- ANSI Colors ---

blue := '\033[1;34m'
green := '\033[1;32m'
nc := '\033[0m'

# Show available commands
default:
    @just --list

# Sync/install dependencies using uv
sync:
    uv sync --quiet

# Auto-format Python and Markdown
format: sync
    @printf "\n{{ blue }}=== Formatting Code ==={{ nc }}\n"
    uv run ruff check --fix .
    uv run ruff format .
    uv run rumdl fmt .
    @printf "{{ green }}✔ Formatting complete{{ nc }}\n"

# Run linters and check formatting (Ruff, Markdown, and GitHub Actions)
lint: sync
    @printf "\n{{ blue }}=== Running Linters ==={{ nc }}\n"
    uv run ruff check .
    uv run ruff format --check .
    uv run rumdl check .
    uv run rumdl fmt --check .
    uv run zizmor --offline .github/
    @printf "{{ green }}✔ Linting passed{{ nc }}\n"

# Run static type checking with Mypy
typecheck: sync
    @printf "\n{{ blue }}=== Running Type Checks ==={{ nc }}\n"
    uv run mypy
    @printf "{{ green }}✔ Type checking passed{{ nc }}\n"

# Download the test corpus's build documents from their publishers
fetch-samples: sync
    uv run python -m tools.fetch_samples

# Run the full automated testing matrix
test: sync fetch-samples
    @printf "\n{{ blue }}=== Running Tests ==={{ nc }}\n"
    uv run pytest -n auto
    @printf "{{ green }}✔ All tests passed{{ nc }}\n"

# Print the parser accuracy scorecard against the golden parts lists
accuracy *args: sync fetch-samples
    uv run python -m tests.accuracy -v {{ args }}

# Fetch a real document from each publisher (needs the internet)
test-network: sync
    uv run pytest -m network -v

# Run tests with coverage
test-cov: sync fetch-samples
    @printf "\n{{ blue }}=== Running Tests with Coverage ==={{ nc }}\n"
    uv run pytest --cov
    @printf "{{ green }}✔ Coverage run complete{{ nc }}\n"

# Find code nothing in the product calls (configured in [tool.vulture])
deadcode: sync
    @printf "\n{{ blue }}=== Finding Dead Code ==={{ nc }}\n"
    uv run vulture

# Run the fast local CI pipeline executed before pushing
ci: lint typecheck test
    @printf "\n{{ green }}✔ Local CI pipeline completed successfully. Clear to push!{{ nc }}\n"

# Remove caches, artifacts, and temp files
clean:
    @printf "\n{{ blue }}=== Cleaning Workspace ==={{ nc }}\n"
    rm -rf \
        .pytest_cache \
        .mypy_cache \
        .ruff_cache \
        htmlcov \
        .coverage \
        coverage.xml \
        coverage_annotations \
        site \
        .benchmarks \
        .cache
    find . -type d -name "__pycache__" -exec rm -rf {} +
    @printf "{{ green }}✔ Workspace cleaned{{ nc }}\n"

# Start the HTTP API, reloading on changes (interactive docs at /api/docs)
serve-api: sync
    uv run uvicorn star_ground.server:app --reload --reload-dir src --host 127.0.0.1 --port 4501

# Regenerate openapi.json and the website's API types after changing src/star_ground/server/
openapi: sync
    uv run python -m tools.export_openapi
    pnpm --dir web run types

# Install locked dependencies and start the website's dev server
serve-web:
    #!/usr/bin/env bash
    set -euo pipefail
    pnpm --dir web install --frozen-lockfile
    if [[ -t 0 ]]; then
        status=$(pnpm --dir web exec astro dev status)
        if [[ $status =~ Dev\ server\ running\ at\ ([^[:space:]]+)\ \(pid\ ([0-9]+), ]]; then
            printf 'Astro is already running at %s (PID %s).\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
            if ! read -r -p 'Stop it and start a new server? [y/N] ' answer; then
                exit 0
            fi
            case "$answer" in
                [Yy]|[Yy][Ee][Ss]) pnpm --dir web exec astro dev stop ;;
                *) exit 0 ;;
            esac
        fi
    fi
    exec pnpm --dir web run dev

# Offer to wait for CI and verify local state before bumping
bump part:
    uv run python -m tools.prepare_release
    uv run --refresh https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-release-infrastructure/refs/heads/main/scripts/release.py {{ part }}
