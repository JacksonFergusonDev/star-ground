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

# The house-style release committed in house/ for the app and future docs.
house_style := "v1.6.0"

# Vendor the pinned release (--check compares without changing files).
sync-house-style *args:
    uv run https://raw.githubusercontent.com/JacksonFergusonDev/house-style/{{ house_style }}/scripts/vendor.py --tag {{ house_style }} house {{ args }}

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

# Run the full automated testing matrix
test: sync
    @printf "\n{{ blue }}=== Running Tests ==={{ nc }}\n"
    uv run pytest -n auto
    @printf "{{ green }}✔ All tests passed{{ nc }}\n"

# Print the parser accuracy scorecard against the golden parts lists
accuracy *args: sync
    uv run python -m tests.accuracy -v {{ args }}

# Fetch a real document from each publisher (needs the internet)
test-network: sync
    uv run pytest -m network -v

# Run tests with coverage
test-cov: sync
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

# Start the Streamlit application
serve: sync
    @printf "\n{{ blue }}=== Launching Streamlit App ==={{ nc }}\n"
    uv run streamlit run app.py

# Refresh presets, offer to wait for CI, and verify local state before bumping
bump part:
    uv run python -m tools.prepare_release
    uv run --refresh https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-release-infrastructure/refs/heads/main/scripts/release.py {{ part }}
