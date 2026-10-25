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

## Running the API

The web frontend talks to the HTTP API in `src/server/`, a FastAPI app over the backend's own API (`src/api.py`). Start it with `just serve-api`, then open `http://127.0.0.1:8000/api/docs` to try each route. These environment variables configure it:

| Variable | What it sets |
| --- | --- |
| `STAR_GROUND_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API (the hosted site). Unset when the site and API share an origin. |
| `STAR_GROUND_REQUESTS_PER_MINUTE` | Each client's request limit on the working routes (default 30). |
| `STAR_GROUND_READ_SLOTS` | Documents read at once; the rest queue (default 2). OCR needs over 1 GB per read. |
| `STAR_GROUND_TRUST_PROXY` | `1` to take the client's address from `X-Forwarded-For`, behind the hosting platform's proxy. |
| `STAR_GROUND_FEEDBACK_CREDENTIALS` | A Google service account's key, as JSON, for the feedback sheet. Without it, feedback answers 503. |

After changing a route or model, run `just openapi` and commit `openapi.json`.

## Working on the Website

`web/` is the website: an Astro site whose app pages will be Svelte components. It needs Node 22.12 or newer.

```bash
cd web
npm ci
npm run dev
```

The site is at `http://127.0.0.1:4321/`. Before opening a pull request, run what CI runs:

```bash
npm run lint
npm run format:check
npm run check
npm run build
npm run test:browser
```

`npm run test:browser` serves the build and runs the Playwright tests in Chromium, Firefox and WebKit, at desktop and phone sizes, including an axe accessibility scan. Install the browsers once with `npx playwright install`.

The frontend's API types in `web/src/lib/api/` are generated from `openapi.json` by `@hey-api/openapi-ts`. After `just openapi`, run `npm run types` in `web/` and commit both files.

## Shared Design and Writing

[house-style](https://github.com/JacksonFergusonDev/house-style) is the shared look and writing rules of Star Ground, Protostar, and jacksonferguson.me. Its `GUIDELINES.md` is the design and writing contract.

The website installs it with npm, pinned to a tag in `web/package.json` (`"house-style": "github:JacksonFergusonDev/house-style#vX.Y.Z"`). It uses three of house-style's parts:

- **core:** fonts, tokens, section markers, buttons, and icons;
- **site:** the project-site shell (header bar, hero field, jump links, section grid, and footer) that Protostar's landing page uses too;
- **astro:** the Astro components that render that shell.

`web/src/layouts/Base.astro` loads the house stylesheets, then `branding/theme.css` (Star Ground's green accent), then `web/src/styles/global.css`, which holds only Star Ground's own page content. To adopt a newer release, run `npm install --save github:JacksonFergusonDev/house-style#vX.Y.Z` in `web/`, then the browser tests. Change shared styles in house-style and release them; never restyle the shell here.

The current Streamlit app keeps its native navy theme in `.streamlit/config.toml` until the rebuilt site replaces it.

When Zensical documentation is added, vendor the parts it uses with house-style's `scripts/vendor.py` (for example `--parts docs,site`), as Protostar does.

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
