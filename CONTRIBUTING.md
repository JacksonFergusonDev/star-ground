# Contributing to Star Ground

First off, thanks for taking the time to contribute! 🎉

## How to Contribute

### Reporting Bugs

1. Check if the issue has already been reported.
1. Open a new issue with a clear title and description.
1. Include the BOM text that caused the error (if applicable).

### Development Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management and Python 3.12+.

1. **Fork & Clone**
   Fork the repo and clone it locally:

   ```bash
   git clone https://github.com/jacksonfergusondev/star-ground.git
   cd git-pulsar
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
   Set up pre-commit hooks to handle linting (Ruff) and type checking (Mypy) automatically.

   ```bash
   pre-commit install
   ```

### Running Tests

We use `pytest` for the test suite.

```bash
uv run pytest
```

### Pull Requests

1. **Create a Branch**

   ```bash
   git checkout -b feature/my-amazing-feature
   ```

1. **Make Changes**
   Write code and add tests for your changes.

1. **Verify**
   Ensure your code passes the linter and tests locally.

   ```bash
   uv run pytest
   ```

   (Pre-commit will also run `ruff` and `mypy` when you commit).

1. **Commit & Push**
   Please use clear commit messages.

   ```bash
   git commit -m "feat: add support for solar flares"
   git push origin feature/my-amazing-feature
   ```

1. **Open a Pull Request**
   Submit your PR against the `main` branch.
