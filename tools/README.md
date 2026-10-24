# Repository Tools

## Preset Generator (`tools/generate_presets.py`)

`tools/generate_presets.py` builds the app's presets library, `src/bom_lib/_presets_data.py`, from the BOM files in `raw_boms/`.

1. Put the files under `raw_boms/<source>/<category>/`, e.g. `raw_boms/pedalpcb/fuzz/Sunflower-PedalPCB.pdf`. The folder names become the source and category the app's preset browser filters by.
1. Run the script from the project root:

   ```bash
   uv run python -m tools.generate_presets
   ```

1. Review the diff of `src/bom_lib/_presets_data.py` and commit it. Never edit that file by hand.

### Supported Formats

- **PDF build documents:** parsed with the app's own parser. A document that describes several circuits becomes one preset per build, named "Title: Build".
- **Text (`.txt`):** a `REF VALUE` list, used as written (the Tayda presets).

## Release Preflight (`tools/prepare_release.py`)

`just bump <major|minor|patch>` runs `tools/prepare_release.py` before invoking the shared release script:

1. Verifies the checkout is on a clean `main` branch synced with `origin/main`.
1. Regenerates `src/bom_lib/_presets_data.py` and offers to commit and push any changes.
1. Requires a passing `ci.yml` run on `HEAD`, offering to watch an in-progress run to completion.
1. Checks `uv lock --check` and `just sync-house-style --check` before handing off to the version bump.
