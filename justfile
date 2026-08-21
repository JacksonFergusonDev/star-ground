set shell := ["bash", "-euc", "-o", "pipefail"]
set unstable
set quiet

# --- ANSI Colors ---

blue := '\033[1;34m'
green := '\033[1;32m'
yellow := '\033[1;33m'
nc := '\033[0m'

# Show available commands
default:
    @just --list

# Sync/install dependencies using uv
sync:
    uv sync --quiet

# Auto-format Python code using Ruff
format: sync
    @printf "\n{{ blue }}=== Formatting Code ==={{ nc }}\n"
    uv run ruff check --fix .
    uv run ruff format .
    @printf "{{ green }}✔ Formatting complete{{ nc }}\n"

# Run linters (Ruff and Markdown)
lint: sync
    @printf "\n{{ blue }}=== Running Linters ==={{ nc }}\n"
    uv run ruff check .
    uv run ruff format --check .
    if command -v markdownlint-cli2 >/dev/null 2>&1; then \
        markdownlint-cli2 "**/*.md"; \
    elif command -v npx >/dev/null 2>&1; then \
        npx --yes markdownlint-cli2 "**/*.md"; \
    else \
        printf "{{ yellow }}⚠ markdownlint-cli2 not found. Skipping markdown linting.{{ nc }}\n"; \
    fi
    @printf "{{ green }}✔ Linting passed{{ nc }}\n"

# Run static type checking with Mypy
typecheck: sync
    @printf "\n{{ blue }}=== Running Type Checks ==={{ nc }}\n"
    uv run mypy .
    @printf "{{ green }}✔ Type checking passed{{ nc }}\n"

# Run the full automated testing matrix
test: sync
    @printf "\n{{ blue }}=== Running Tests ==={{ nc }}\n"
    uv run pytest
    @printf "{{ green }}✔ All tests passed{{ nc }}\n"

# Run tests with coverage
test-cov: sync
    @printf "\n{{ blue }}=== Running Tests with Coverage ==={{ nc }}\n"
    uv run pytest --cov
    @printf "{{ green }}✔ Coverage run complete{{ nc }}\n"

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
