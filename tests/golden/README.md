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

# score an external (e.g. local, uncommitted) corpus with the same machinery
uv run python -m tests.accuracy --golden path/to/golden --samples path/to/pdfs
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

A document-level `NO_PARTS_LIST` warning (nothing with a designator could be
read, e.g. an image-only parts table) flags every part of that document, and so
does `UNREADABLE_ROW` (an OCR'd row whose designator could not be read).

A part listed under a misread name is not a silent miss when the right part was
read and warned: OCR reads `OUTPUT1` as `OUPUT1`, with the right value and a
`Read by OCR` warning. A warned extra part of the same category and value stands
in for one missing part. An unwarned extra never does.

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
- `accept`: alternative values for the same designator that the document
  allows (a substitute in the notes column, a "recommended" taper).
- `unspecified`: `true` when the document leaves the value to the builder
  (Sheepylove's `R1  CLR`: an LED resistor of your choice). The value is
  recorded as printed, and a warning about it is correct, not a false alarm.
- `absent`: designators the document lists but marks as not populated (`N/A`,
  `OMIT`, a wire jumper). Emitting one counts as an extra part.
- `count`: how many to buy under one designator when two boards in one
  document list it with the same value (EV Mini's Q1-Q4: `2`). Defaults to 1;
  emitting a designator a different number of times is a `wrong_count`
  failure, so a table read twice cannot hide.
- `R1 (2)`: the second listing of a designator with *another* value (a second
  board, or one label used for two parts). A warning on `R1` covers `R1 (2)`,
  and both are ambiguous, so the warning is not a false alarm.
- `options`: interchangeable choices that use different designators (see below).
- `variants`: replaces `parts` when the document describes several circuits:

```json
{
  "variants": {
    "Germanium": {"parts": {...}, "absent": ["R6", "D3"]},
    "Silicon": {"parts": {...}, "absent": ["D2"]}
  }
}
```

### Variants, Options, and Alternate Values

Documents offer the builder three kinds of choice. They are recorded differently
because the app must handle them differently:

| Kind | Example | App behavior | Golden format |
|---|---|---|---|
| **Variant**: a different circuit (changes sound or controls) | Muffin Fuzz's eight versions; Fuchsia Fuzz's Nature *or* Treble+Bass; a "Cleft mod" | Ask the builder which one | `variants` |
| **Option**: interchangeable parts filling the same spot | Through-hole Q1-Q6 *or* SMD Q7-Q12; a bridge rectifier *or* four diodes | Use the first choice, note the alternative | `options` |
| **Alternate value**: same designator, another acceptable value | `B1M` (A1M recommended); OPA1678 or OPA2134 | Use the listed value, note the alternative | `accept` |

An option group maps a group name to its choices, each a parts mapping; the
first choice is the document's default:

```json
"options": {
  "D10": {
    "Bridge rectifier": {"D10": {"category": "Diodes", "value": "DI1510"}},
    "Discrete diodes": {"D10a": {...}, "D10b": {...}, "D10c": {...}, "D10d": {...}}
  }
}
```

Scoring: every variant is graded as its own build, so the score says whether a
builder of *each* variant gets the right parts. Golden variants are paired
one-to-one with the parser's builds by agreement (ties in document order). A
variant the parser did not produce (a third version described in prose as a
list of replacements) is graded against the build it agrees with most, the
list its builder would start from. If the parser produced a single build, every
variant is graded against it. Failures
are keyed `Build/REF`. An option group is satisfied by any one choice; emitting
parts from two choices counts the second as extra, because the shopping list
would buy both. Warnings on any of these designators are correct behavior, not
false alarms.

## Labeling Rules

1. **Record what the builder must buy.** When the document gives a more specific
   part elsewhere (a TYPE column, a page-5 BOM), use it: Kliche's `GE` diodes
   are `1N34A` because its TYPE column says so.
1. **Keep purchase-relevant qualifiers in the value:** dielectric (`1u MLCC`,
   or `220n Tantalum` when only the TYPE column names it),
   voltage rating (`10u (25V or higher)`), LED size and color, dual-gang pots,
   and switch throw (`SPDT (On/Off/On)`).
1. **Drop words that only restate the category** (`Crystal`) and markers that
   are not part of the value (optional-part asterisks).
1. **Follow the parts list over the schematic** when they conflict, and write
   the conflict in `notes`.
1. **Scope is exactly the document's parts list.** Anything it lists is a part,
   including jacks, footswitches, and DC jacks when they appear (Expresso,
   Arkaim). Anything it does not list is out of scope; most documents state
   "common offboard components are not listed".
1. **LEDs are Diodes; LDRs are Optoelectronics; trimmers are Potentiometers.**
   Parts with no matching category (transformers, inductors, relays, jacks)
   are Hardware/Misc.
1. **Trimmers record the resistance only** (`10K`, not `10K (3362P Trim)`).
1. **Switches record poles/throws and positions** (`SPDT (On/On)`), completing a
   bare position value from the TYPE column; the printed form (`SPDT Toggle
   (On/On)`) goes in `accept`.
1. **Generic values stay as printed** when the document names no specific part
   (`GE`, `Indicator LED`, `LDR`, `5V1 Zener`).
1. **The title is the document's own title** (page 1), never the filename.

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
