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

## Shared Design and Writing

The `house/` directory is a committed release of [house-style](https://github.com/JacksonFergusonDev/house-style). Its `GUIDELINES.txt` is the design and writing contract. The `house_style` variable in the `justfile` is the release pin, and `branding/theme.css` supplies Star Ground's green accent.

To adopt a newer release, change the pin, then run:

```sh
just sync-house-style
just sync-house-style --check
```

Commit the pin and the complete vendored copy together. CI runs the check and fails if files were changed, added, or removed. Never edit `house/` directly; make shared design changes in house-style and adopt its release here.

The current Streamlit app keeps its native theme in `.streamlit/config.toml`, using the product palette's navy background, panel, and text colors with Star Ground's green accent. The vendored CSS is not loaded into Streamlit. This setup leaves the current app's fonts and layout unchanged.

The JavaScript/React frontend and Zensical documentation have not been created. They will share this same pinned copy:

- The frontend should import `house/css/fonts.css`, `house/css/tokens.css`, `house/css/product.css`, and the component styles it uses, then `branding/theme.css` and its layout styles. Let the bundler copy and fingerprint referenced fonts and icons.
- The docs build should copy `house/` unchanged to an ignored `docs/house/` staging directory and copy `branding/theme.css` to its own served assets. Load the house files first, then the brand stylesheet and the documentation theme adapter. Disable the theme's external font loading and use the house fonts. Protostar's `zensical.toml` and `docs/stylesheets/extra.css` show how to map house tokens to Zensical.

Keep the release pin and vendored source shared rather than maintaining separate copies for each build. Site-specific CSS handles layout and the brand accent; shared components and rules change upstream.

## Running Tests

We use `pytest` for the test suite. The test corpus's PDFs belong to their publishers and aren't in the repository: `just test` downloads them first (listed with their hashes in `tests/samples/manifest.json`), and `just fetch-samples` does only that.

```bash
just test
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
   just test
   ```

1. **Commit & Push**

   Use [Conventional Commits](https://www.conventionalcommits.org/) titles (`feat(parser): ...`, `fix(app): ...`).

   ```bash
   git commit -m "feat: add support for solar flares"
   git push origin feature/my-amazing-feature
   ```

1. **Open a Pull Request**

   Submit your PR against the `main` branch. Explain the problem and why you solved it the way you did, not just what changed (see [AGENTS.md](AGENTS.md)).
