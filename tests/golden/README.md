# Golden Parts Lists

Each `*.json` file here is a hand-verified transcription of one sample PDF in
`tests/samples/`. These files are the ground truth the parser is graded against
by `tests/accuracy.py`. Unlike the snapshots in `tests/snapshots/`, they are
never generated from parser output.

## Workflow

```bash
uv run python -m tests.accuracy            # scorecard
uv run python -m tests.accuracy -v         # every failure, warned or silent
uv run python -m tests.accuracy Distortr   # filter by document name
uv run python -m tests.accuracy --update-baseline
```

`baseline.json` records every known failure and whether the parser warned about
it. `tests/test_parser_accuracy.py` fails on any difference from it:

- **Regressions** (a new wrong part, a failure that is no longer warned about,
  or a warning on a correctly parsed part) must be fixed.
- **Improvements** must be locked in with `--update-baseline`, so they can never
  quietly regress later.

## Scorecard Columns

- **correct / recall:** designators graded correct out of all designators in the
  golden file.
- **precision:** correct designators out of all designators the parser emitted.
- **silent:** failures the user was not warned about. This is the trust metric:
  a wrong part with a warning gets caught by the builder; a wrong part without
  one gets ordered.
- **false alarms:** warnings on parts that were parsed correctly. Noisy
  warnings teach users to ignore them, so this must stay at zero.

## File Format

```json
{
  "pdf": "dirty/Distortr.pdf",
  "title": "Distortr",
  "notes": ["Free-text explanation of every judgment call."],
  "absent": ["D3"],
  "parts": {
    "R1": {"category": "Resistors", "value": "1M"},
    "SW1": {"category": "Switches", "value": "2P4T Mini Rotary", "accept": ["SPDT ON/OFF/ON"]}
  }
}
```

- `parts`: every designator in the document's parts list.
- `accept`: alternatives the document explicitly allows (an "OR" row, a
  substitute in the notes column, a mod variant).
- `absent`: designators the document lists but marks as not populated (`N/A`,
  `OMIT`). Emitting one counts as an extra part.

## Labeling Rules

1. **Record what the builder must buy.** When the document gives a more specific
   part elsewhere (a TYPE column, a page-5 BOM), use it: Kliche's `GE` diodes
   are `1N34A` because its TYPE column says so.
1. **Keep purchase-relevant qualifiers in the value:** dielectric (`1u MLCC`),
   voltage rating (`10u (25V or higher)`), LED size and color, dual-gang pots,
   and switch throw (`SPDT (On/Off/On)`).
1. **Drop words that only restate the category** (`Crystal`) and markers that
   are not part of the value (optional-part asterisks).
1. **Follow the parts list over the schematic** when they conflict, and write
   the conflict in `notes`.
1. **Off-board hardware is out of scope** (enclosure, jacks, footswitch, DC
   jack). PedalPCB documents generally do not list it.
1. **LEDs are Diodes; LDRs are Optoelectronics; trimmers are Potentiometers.**

## Adding a Document

1. Drop the PDF into `tests/samples/clean/` or `tests/samples/dirty/`.
1. Transcribe its parts list into `tests/golden/<folder>__<name>.json` from the
   rendered page, not from parser output. Record every judgment call in
   `notes`.
1. Run `uv run python -m tests.accuracy -v <name>` and check each failure against
   the PDF. A failure is either a parser bug or a golden-file mistake; fix the
   golden file only for the latter.
1. Run `--update-baseline` and commit the golden file and baseline together.

Comparison is normalization-aware (`4K7` equals `4.7k`, `A100K` equals
`100KA`), so transcribe values as printed.
