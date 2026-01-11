# Contributing to Pedal BOM Manager

First off, thanks for taking the time to contribute! 🎉

## How to Contribute

### Reporting Bugs
1. Check if the issue has already been reported.
2. Open a new issue with a clear title and description.
3. Include the BOM text that caused the error (if applicable).

### Development Setup
1. Fork the repo and clone it locally.

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. Install development dependencies:
    ```bash
    pip install -r requirements-dev.txt
    ```

4. Install pre-commit hooks:
    ```bash
    pre-commit install
    ```

### Pull Requests:

1. Create a branch for your feature
    ```bash
    git checkout -b feature/amazing-feature
    ```

2. Commit your changes

3. Run tests locally (pytest).

4. Push to the branch.

5. Open a Pull Request.

6. Please ensure your code passes the linting checks (Ruff/Mypy).