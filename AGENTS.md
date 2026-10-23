# Star Ground Agent Guidelines

Guidelines and architectural invariants for AI agents working in the Star Ground codebase.

## Development Phase & Backwards Compatibility (Pre-1.0)

- **Zero External Users:** Star ground is in active pre-1.0 development with no external users or production stability commitments.
- **Do NOT preserve backwards compatibility:** Do not create deprecation warnings, alias wrappers, fallback shims, or legacy compatibility layers.
- **Break and delete cleanly:** If an internal or public API, CLI flag, schema, or configuration model needs improvement, change or break it directly. Delete obsolete code and dead parameters outright rather than carrying tech debt.
- **Prioritize ergonomics:** Clean, simple, and idiomatic architecture always takes precedence over historical continuity.

## Pre-Commit & Pre-Push Hooks (Avoid Redundant Checks)

The repository uses **`prek`** hooks (`.pre-commit-config.yaml`) for automated gating:

- **On `git commit` (pre-commit):** Automatically runs `uv lock --check`, `ruff check --fix`, `ruff format`, `mypy`, and `rumdl check/fmt`.
- **On `git push` (pre-push):** Automatically runs `pytest-unit`.

> **Agent Rule:** **Do NOT redundantly run `ruff`, `mypy`, `rumdl`, `just lint`, or `just ci` immediately before committing or pushing.** Let the hooks do the work. If a hook fails or formats a file, inspect the failure, adjust the code, and re-stage. Only run manual commands during active development/debugging (e.g. running a specific test file like `uv run pytest tests/test_foo.py`).

## Code & Testing Conventions

1. **Strict Type Safety & Domain Modeling:**
   - Python 3.12+ type hints are enforced across `src/` (`mypy --strict`).
   - **No Stringly-Typed APIs:** Use `enum.StrEnum` or `enum.Enum` for discrete choices, modes, strategies, or status values (e.g., `CollisionStrategy`). Avoid magic strings.
   - **No Data Clumps / Primitive Obsession:** Group related parameters into `@dataclass(frozen=True)` or `TypedDict` models instead of passing loose tuples, untyped dictionaries (`dict[str, Any]`), or 4+ primitive arguments.
   - **Lean Modeling (Avoid Over-Engineering):** Strong typing means precise data structures, *not* elaborate architecture. Avoid speculative generic abstractions, factory classes, or deep inheritance trees. Prefer flat dataclasses, enums, and pure functions.
1. **Google-Style Docstrings:** Required on all public functions, classes, and methods.
1. **Filesystem Isolation in Tests:**
   - **Never** write to the host filesystem during tests. Always inject the `pytest` `tmp_path` fixture.
   - **Never** run live shell commands or package managers (`uv`, `git`, `cargo`) on the host system during tests. Patch `subprocess.run` with `pytest-mock`.
1. **Markdown Standards (`rumdl`):**
   - ATX headings only (`# Heading`, never underlined).
   - Dash markers (`-`) for unordered lists.
   - `1.` numbering for all ordered list items.
1. **Dead Code (On Request):** When asked to look for dead code, run `just deadcode` (vulture, configured in `[tool.vulture]`). It is not a gate: it exits non-zero whenever it reports anything, and findings are leads to review, not errors to clear. It scans `app.py`, `src/`, and `tools/` but not `tests/`, so code only tests reach shows up. Confirm each finding before acting (grep for string keys, `getattr`, and hooks a library calls). Delete real dead code along with the tests that only exercise it. Add a confirmed false positive to `ignore_names` under a comment giving the reason, never a lower `min_confidence`.

## Shared Design and Writing

[house-style](https://github.com/JacksonFergusonDev/house-style) supplies the shared design and writing rules. Read `house/GUIDELINES.txt` before changing prose or UI. The release is pinned by `house_style` in the `justfile`.

- **`house/` is vendored, never edited.** Run `just sync-house-style` after changing the tag, commit the complete result, and use `just sync-house-style --check` to verify it. CI checks that the copy matches the release. Change shared styles and rules in house-style first, then adopt a tagged release here.
- **Star Ground's accent is green.** `branding/theme.css` sets `--accent: #4ade80`. The shared navy surfaces come from `house/css/product.css`. Keep the logo assets in this repository.
- **Streamlit uses its native theme.** `.streamlit/config.toml` maps the shared palette to Streamlit's settings. Vendoring house-style does not load CSS into Streamlit. This adoption preserves the current app's typography and layout; do not inject house CSS or target Streamlit's internal DOM to imitate shared components.
- **The React frontend and Zensical docs are planned.** Do not scaffold them as part of maintaining the vendored release. When the frontend is introduced, load house fonts, tokens, the product palette, and the components it uses, followed by `branding/theme.css` and layout styles. When docs are introduced, stage an unchanged copy of `house/` under `docs/house/` during the docs build, load that copy through Zensical, and map its tokens to the documentation theme as Protostar does. Stage the brand stylesheet too. Keep the staged copies untracked and keep one pinned source in `house/`.
- **Apply the writing rules to new prose and edits.** Use the General audience for the README and future landing pages, Users for instructions, and Technical for contributor and implementation documentation. Existing prose can be brought into the guidelines as it is edited.

## Pull Request & Architecture Documentation

PR descriptions are the primary historical record of the project's evolution. **Do not merely summarize the `git diff` or list *what* code changed.** You must tell the full story of the PR's planning, decision-making, and architectural impact.

- **Conventional Commit Titles:** Use standard Conventional Commits format for PR titles (e.g., `feat(cli): ...`, `refactor(manifest): ...`).
- **Tell the Story (The "Why" and "How"):** Instead of describing file modifications, explain the original problem, the planning process, and the journey to the final solution.
- **Decision Rationale:** Detail the reasoning behind major technical choices. Explain what alternatives were considered and rejected, and why the final path was chosen.
- **Defend Architectural Boundaries (When Applicable):** When a PR establishes, modifies, or reinforces an architectural boundary, contract, or invariant, it must be explicitly documented. **Do not invent trivial boundaries for minor changes just to check a box.** If no architectural changes occurred, do not mention them.
- **Calibrated Verbosity:** Scale your explanation to the scope of the PR. Minor cleanups should be extremely brief; major architectural shifts require highly verbose, comprehensive narratives.

### Expected PR Structure

Scale or omit these sections based on the scope of the PR.

1. **Summary & Motivation:** A concise summary of the goal and the core problem being solved.
1. **Planning & Key Decisions:** The narrative of the design process. What did we discuss? Why did we build it this way? *(Keep very brief for simple PRs).*
1. **Architectural Invariants (Conditional):** Explicitly define any new boundaries, assumptions, or structural rules established by this PR. **Omit this section entirely if not applicable.**
