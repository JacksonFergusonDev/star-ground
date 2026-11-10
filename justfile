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

# Install Python and website dependencies
sync: _sync-python _sync-web

_sync-python:
    uv sync --quiet

_sync-web:
    pnpm --dir web install --frozen-lockfile

# Auto-format Python and Markdown
format: _sync-python
    @printf "\n{{ blue }}=== Formatting Code ==={{ nc }}\n"
    uv run ruff check --fix .
    uv run ruff format .
    uv run rumdl fmt .
    @printf "{{ green }}✔ Formatting complete{{ nc }}\n"

# Run linters and check formatting (Ruff, Markdown, and GitHub Actions)
lint: _sync-python
    @printf "\n{{ blue }}=== Running Linters ==={{ nc }}\n"
    uv run ruff check .
    uv run ruff format --check .
    uv run rumdl check .
    uv run rumdl fmt --check .
    uv run zizmor --offline .github/
    @printf "{{ green }}✔ Linting passed{{ nc }}\n"

# Run static type checking with Mypy
typecheck: _sync-python
    @printf "\n{{ blue }}=== Running Type Checks ==={{ nc }}\n"
    uv run mypy
    @printf "{{ green }}✔ Type checking passed{{ nc }}\n"

# Download the test corpus's build documents from their publishers
fetch-samples: _sync-python
    uv run python -m scripts.fetch_samples

# Run the full automated testing matrix
test: _sync-python fetch-samples
    @printf "\n{{ blue }}=== Running Tests ==={{ nc }}\n"
    uv run pytest -n auto
    @printf "{{ green }}✔ All tests passed{{ nc }}\n"

# Print the parser accuracy scorecard against the golden parts lists
accuracy *args: _sync-python fetch-samples
    uv run python -m tests.accuracy -v {{ args }}

# Fetch a real document from each publisher (needs the internet)
test-network: _sync-python
    uv run pytest -m network -v

# Build the image and run the container gate against it (needs Docker running)
test-container: _sync-python
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker info > /dev/null 2>&1; then
        printf 'The container check needs Docker, and Docker is not running here.\n' >&2
        printf 'Start Docker and push again. CI runs this check on the pull request either way.\n' >&2
        exit 1
    fi
    printf "\n{{ blue }}=== Testing the Container ==={{ nc }}\n"
    uv run playwright install chromium
    docker build --tag star-ground:smoke .
    artifacts=$(mktemp -d)
    uv run python -m scripts.smoke_container star-ground:smoke --artifacts "$artifacts"
    printf "{{ green }}✔ Container check passed{{ nc }}\n"

# Run tests with coverage
test-cov: _sync-python fetch-samples
    @printf "\n{{ blue }}=== Running Tests with Coverage ==={{ nc }}\n"
    uv run pytest --cov
    @printf "{{ green }}✔ Coverage run complete{{ nc }}\n"

# Find code nothing in the product calls (configured in [tool.vulture])
deadcode: _sync-python
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
serve-api: _sync-python
    uv run uvicorn star_ground.server:app --reload --reload-dir src --host 127.0.0.1 --port 4501

# Regenerate examples/build-pack/ from the Big Muff π netlist
example-pack: _sync-python
    uv run python -m scripts.example_pack

# Regenerate openapi.json and the website's API types after changing src/star_ground/server/
openapi: sync
    uv run python -m scripts.export_openapi
    pnpm --dir web run types

# Install locked dependencies and start the website's dev server
serve-web: _sync-web
    #!/usr/bin/env bash
    set -euo pipefail
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

# The shared release script, pinned to a commit so `just bump` runs reviewed code.
# To adopt a newer version, read its diff and replace the commit here.
release_script := 'https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-release-infrastructure/0f7ac0b09417daed4bf3ee42407351bb285b1cff/scripts/release.py'

# Offer to wait for CI and verify local state before bumping
bump part:
    uv run python -m scripts.prepare_release
    uv run --refresh {{ release_script }} {{ part }}
