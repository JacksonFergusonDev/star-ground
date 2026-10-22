# Preset Generator Tool

`tools/generate_presets.py` builds the app's presets library, `src/bom_lib/_presets_data.py`, from the BOM files in `raw_boms/`.

## Usage

1. Put the files under `raw_boms/<source>/<category>/`, e.g. `raw_boms/pedalpcb/fuzz/Sunflower-PedalPCB.pdf`. The folder names become the source and category the app's preset browser filters by.
1. Run the script from the project root:

   ```bash
   uv run python tools/generate_presets.py
   ```

1. Review the diff of `src/bom_lib/_presets_data.py` and commit it. Never edit that file by hand.

## Supported Formats

- **PDF build documents:** parsed with the app's own parser. A document that describes several circuits becomes one preset per build, named "Title: Build".
- **Text (`.txt`):** a `REF VALUE` list, used as written (the Tayda presets).
