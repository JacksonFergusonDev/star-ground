# Contributing to Star Ground

## Reporting Bugs

1. Check if the issue has already been reported.
1. Open a new issue with a clear title and description.
1. Include the document or BOM text that caused it, or a link to it. For a wrong parts list, name the parts that are wrong and what the document says.

## Development Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management and Python 3.14.

1. **Fork & Clone**

   Fork the repo and clone it locally:

   ```bash
   git clone https://github.com/jacksonfergusondev/star-ground.git
   cd star-ground
   ```

1. **Environment Setup**

   We use `uv` to manage the virtual environment and dependencies.

   ```bash
   # Creates .venv and installs dependencies (including dev groups)
   uv sync
   ```

   *Optional: If you use `direnv`, allow the automatically generated configuration:*

   ```bash
   direnv allow
   ```

1. **Install Hooks**

   Set up the prek hooks. On commit they run `ruff`, `mypy` and `rumdl` (Markdown); on push, the unit tests.

   ```bash
   prek install
   ```

## Running Tests

We use `pytest` for the test suite.

```bash
uv run pytest
```

Parser changes are graded against hand-labeled parts lists; print the scorecard with `just accuracy`. See [tests/README.md](tests/README.md) and [tests/golden/README.md](tests/golden/README.md).

## Pull Requests

1. **Create a Branch**

   ```bash
   git checkout -b feature/my-amazing-feature
   ```

1. **Make Changes**

   Write code and add tests for your changes.

1. **Verify**

   Run the tests locally. The hooks lint and type-check on commit, so there's no need to run those by hand.

   ```bash
   uv run pytest
   ```

1. **Commit & Push**

   Use [Conventional Commits](https://www.conventionalcommits.org/) titles (`feat(parser): ...`, `fix(app): ...`).

   ```bash
   git commit -m "feat: add support for solar flares"
   git push origin feature/my-amazing-feature
   ```

1. **Open a Pull Request**

   Submit your PR against the `main` branch. Explain the problem and why you solved it the way you did, not just what changed (see [AGENTS.md](AGENTS.md)).
