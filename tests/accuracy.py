"""Ground-truth accuracy scoring for the PDF parser.

Each file in ``tests/golden/`` is a hand-verified transcription of one sample
PDF's parts list: every designator, its category, and the value a builder must
actually purchase. This module runs the production parser against each sample
and grades every designator against that truth.

Unlike the snapshot suite (which only proves the output did not *change*), this
answers whether the output is *correct*, and whether each mistake was flagged to
the user by a parse warning or slipped through silently.

Run ``uv run python -m tests.accuracy`` for a scorecard, or
``uv run python -m tests.accuracy --update-baseline`` to lock in improvements.
``--golden DIR --samples DIR`` scores an external corpus (e.g. a local,
uncommitted one) with the same machinery. ``--ocr`` scores only the documents
OCR reads (`src/bom_lib/ocr.py`), with its parts accepted as a builder would.
"""

import argparse
import functools
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict

from src.bom_lib import (
    ComponentCategory,
    Inventory,
    OcrAttempt,
    OcrVerdict,
    ParseResult,
    ParseWarningKind,
    parse_pedalpcb_pdf,
)
from src.bom_lib.ocr import attempt_ocr
from src.bom_lib.utils import parse_value_to_decimal

TESTS_DIR = Path(__file__).parent
GOLDEN_DIR = TESTS_DIR / "golden"
SAMPLES_DIR = TESTS_DIR / "samples"
BASELINE_PATH = GOLDEN_DIR / "baseline.json"

_POT_PATTERN = re.compile(
    r"^(?P<pre>[ABCWG])?(?P<num>\d+(?:\.\d+)?[KM]?\d*)-?(?P<post>[ABC])?(?:\s+|$)(?P<rest>.*)$"
)


class Outcome(StrEnum):
    """Grade assigned to a single designator."""

    CORRECT = "correct"
    MISSING = "missing"
    WRONG_CATEGORY = "wrong_category"
    WRONG_VALUE = "wrong_value"
    WRONG_COUNT = "wrong_count"
    EXTRA = "extra"


@dataclass(frozen=True)
class ExpectedPart:
    """Ground-truth classification for one designator.

    Attributes:
        category: The category the part belongs in.
        value: The value a builder must purchase.
        accept: Equivalent alternatives the document explicitly allows.
        unspecified: The document leaves the value to the builder (Sheepylove's
            'R1  CLR': choose an LED resistor), so warning about it is correct.
        count: How many to buy under this designator: 2 when two boards in one
            document both list it with the same value (EV Mini's Q1-Q4).
    """

    category: ComponentCategory
    value: str
    accept: tuple[str, ...] = ()
    unspecified: bool = False
    count: int = 1


@dataclass(frozen=True)
class ActualPart:
    """What the parser produced for one designator."""

    category: ComponentCategory
    value: str
    count: int = 1


DEFAULT_VARIANT = "default"


@dataclass(frozen=True)
class OptionGroup:
    """Interchangeable ways to fill the same spot on the board.

    Unlike variants, options do not change the circuit: through-hole or SMD
    transistors, a bridge rectifier or four discrete diodes. Choices may use
    different designators (D10 vs D10a-D10d), so they cannot be expressed as
    ``accept`` alternatives on a single designator. The first choice is the
    document's default.

    Attributes:
        name: Short label for the group (e.g. 'D10').
        choices: Choice name to the parts that choice requires, in document order.
    """

    name: str
    choices: dict[str, dict[str, ExpectedPart]]

    @property
    def refs(self) -> frozenset[str]:
        """Every designator used by any choice."""
        return frozenset(ref for parts in self.choices.values() for ref in parts)


@dataclass(frozen=True)
class Variant:
    """One buildable circuit described by a document.

    Most documents describe exactly one circuit. Some list several versions
    side by side (e.g. Muffin Fuzz), each with its own parts list.

    Attributes:
        name: Variant name as printed ('default' for single-circuit documents).
        parts: Expected parts keyed by upper-cased designator, excluding options.
        absent: Designators listed but marked as not populated.
        options: Groups of interchangeable choices; exactly one choice per group
            belongs in a correct parts list.
    """

    name: str
    parts: dict[str, ExpectedPart]
    absent: frozenset[str]
    options: tuple[OptionGroup, ...] = ()


@dataclass(frozen=True)
class GoldenDoc:
    """Ground truth for one sample PDF.

    Attributes:
        name: Golden file stem (e.g. 'dirty__Distortr').
        pdf: Path to the sample PDF.
        title: The project title printed on the document.
        variants: Every circuit the document describes, in document order.
    """

    name: str
    pdf: Path
    title: str
    variants: tuple[Variant, ...]

    @property
    def ambiguous_refs(self) -> frozenset[str]:
        """Designators whose correct value is a choice the builder makes.

        Includes documented alternates (``accept``), values the document
        leaves unspecified, option choices, and
        designators whose value differs between variants. Warning about these
        is correct behavior.
        """
        refs = {
            ref
            for v in self.variants
            for ref, part in v.parts.items()
            if part.accept or part.unspecified
        }
        refs |= {ref for v in self.variants for g in v.options for ref in g.refs}
        all_refs = {ref for v in self.variants for ref in v.parts}
        # A designator listed twice ('R1' and 'R1 (2)': two boards, or one label
        # used for two parts) is ambiguous until the builder checks the board.
        repeated = {m.group(1) for ref in all_refs if (m := _REPEAT.match(ref))}
        refs |= {ref for ref in all_refs if ref in repeated or _REPEAT.match(ref)}
        for ref in all_refs:
            seen = {
                (v.parts[ref].category, v.parts[ref].value) if ref in v.parts else None
                for v in self.variants
            }
            if len(seen) > 1:
                refs.add(ref)
        return frozenset(refs)


@dataclass(frozen=True)
class RefResult:
    """Grade for one designator, plus whether the parser warned about it.

    Attributes:
        build: The golden variant this grade belongs to ('' for single-circuit
            documents).
    """

    ref: str
    outcome: Outcome
    expected: ExpectedPart | None
    actual: ActualPart | None
    flagged: bool
    build: str = ""

    @property
    def key(self) -> str:
        """Unique label within a document: 'REF', or 'Build/REF' for variants."""
        return f"{self.build}/{self.ref}" if self.build else self.ref

    def describe(self) -> str:
        """Returns a one-line human-readable explanation of the mismatch."""
        exp, act = self.expected, self.actual
        if self.outcome == Outcome.MISSING and exp:
            return f"missing (expected {exp.category.value} | {exp.value})"
        if self.outcome == Outcome.EXTRA and act:
            return f"extra ({act.category.value} | {act.value})"
        if self.outcome == Outcome.WRONG_CATEGORY and exp and act:
            return f"category: expected {exp.category.value}, got {act.category.value}"
        if self.outcome == Outcome.WRONG_VALUE and exp and act:
            return f"value: expected {exp.value!r}, got {act.value!r}"
        if self.outcome == Outcome.WRONG_COUNT and exp and act:
            return f"count: expected {exp.count}, got {act.count}"
        return self.outcome.value


@dataclass(frozen=True)
class DocScore:
    """All designator grades for one document."""

    name: str
    results: tuple[RefResult, ...]
    title_expected: str
    title_actual: str | None
    warned_refs: frozenset[str]
    ambiguous_refs: frozenset[str] = frozenset()
    variant_count: int = 1
    builds_found: int = 1
    # Set when OCR read part of the document (`--ocr`).
    ocr_verdict: OcrVerdict | None = None
    ocr_refs: frozenset[str] = frozenset()

    @property
    def expected_count(self) -> int:
        """Number of designators in the ground truth."""
        return sum(1 for r in self.results if r.outcome != Outcome.EXTRA)

    @property
    def predicted_count(self) -> int:
        """Number of designators the parser emitted."""
        return sum(1 for r in self.results if r.outcome != Outcome.MISSING)

    @property
    def correct(self) -> int:
        """Number of designators graded correct."""
        return sum(1 for r in self.results if r.outcome == Outcome.CORRECT)

    @property
    def failures(self) -> tuple[RefResult, ...]:
        """All non-correct grades."""
        return tuple(r for r in self.results if r.outcome != Outcome.CORRECT)

    @property
    def silent_failures(self) -> tuple[RefResult, ...]:
        """Failures the parser did not warn the user about."""
        return tuple(r for r in self.failures if not r.flagged)

    @property
    def false_alarms(self) -> frozenset[str]:
        """Designators the parser warned about that were unambiguously correct.

        Warnings on ambiguous parts (documented alternates, or values that
        differ between variants) are not false alarms: telling the user about
        the choice is the right behavior.
        """
        # Every part OCR read is warned about by design: OCR is never trusted.
        return (
            self.warned_refs
            - {r.ref for r in self.failures}
            - self.ambiguous_refs
            - self.ocr_refs
        )

    @property
    def title_ok(self) -> bool:
        """Whether the extracted title matches the document title."""
        return _text_key(self.title_actual or "") == _text_key(self.title_expected)


def _text_key(value: str) -> str:
    """Case-, spacing-, and punctuation-insensitive comparison key."""
    folded = unicodedata.normalize("NFKC", value).upper()
    return re.sub(r"[^A-Z0-9]", "", folded)


def _split_head(value: str) -> tuple[str, str]:
    head, _, rest = value.strip().partition(" ")
    return head, rest


_VOLTAGE = re.compile(r"(\d+(?:\.\d+)?)\s*V\b", re.IGNORECASE)
# Words that phrase a voltage rating without changing it:
# '(25V or higher)', '(25V minimum)', '25V min' all mean a 25V-rated part.
_RATING_FILLER = frozenset(
    {"OR", "HIGHER", "MINIMUM", "MIN", "VOLTAGE", "RATED", "AT", "LEAST"}
)


def _qualifiers(rest: str) -> tuple[frozenset[Decimal], str]:
    """Splits trailing qualifiers into voltage ratings and the remaining text."""
    folded = unicodedata.normalize("NFKC", rest).upper()
    volts = frozenset(Decimal(v) for v in _VOLTAGE.findall(folded))
    words = [w for w in re.split(r"[^A-Z0-9.]+", _VOLTAGE.sub(" ", folded)) if w]
    if volts:
        words = [w for w in words if w not in _RATING_FILLER]
    return volts, "".join(words)


def _passive_key(value: str) -> tuple[Decimal | str, frozenset[Decimal], str]:
    head, rest = _split_head(value)
    dec = parse_value_to_decimal(head)
    return (dec if dec is not None else _text_key(head), *_qualifiers(rest))


def _pot_key(value: str) -> tuple[str, Decimal | str, str]:
    norm = unicodedata.normalize("NFKC", value).upper().strip()
    norm = re.sub(r"[()]", "", norm)
    m = _POT_PATTERN.match(norm)
    if not m:
        return ("", _text_key(norm), "")
    taper = m.group("pre") or m.group("post") or ""
    dec = parse_value_to_decimal(m.group("num"))
    return (
        taper,
        dec if dec is not None else m.group("num"),
        _text_key(m.group("rest")),
    )


def values_match(category: ComponentCategory, expected: str, actual: str) -> bool:
    """Compares two values under category-appropriate normalization.

    Resistors and capacitors compare numerically (so '4K7' == '4.7k') and then
    compare any trailing qualifiers ('MLCC', voltage ratings) as text.
    Potentiometers compare taper, resistance, and qualifiers ('DUAL').
    Everything else compares as case- and punctuation-insensitive text.

    Args:
        category: The expected component category.
        expected: The ground-truth value.
        actual: The parser's value.

    Returns:
        True if the values describe the same purchasable part.
    """
    if category in (ComponentCategory.RESISTORS, ComponentCategory.CAPACITORS):
        return _passive_key(expected) == _passive_key(actual)
    if category == ComponentCategory.POTENTIOMETERS:
        return _pot_key(expected) == _pot_key(actual)
    return _text_key(expected) == _text_key(actual)


def _load_parts(raw: dict[str, dict[str, object]]) -> dict[str, ExpectedPart]:
    return {
        ref.upper(): ExpectedPart(
            category=ComponentCategory(str(spec["category"])),
            value=str(spec["value"]),
            accept=tuple(str(a) for a in spec.get("accept", ())),  # type: ignore[attr-defined]
            unspecified=bool(spec.get("unspecified", False)),
            count=int(spec.get("count", 1)),  # type: ignore[call-overload]
        )
        for ref, spec in raw.items()
    }


def _load_options(
    raw: dict[str, dict[str, dict[str, dict[str, object]]]],
) -> tuple[OptionGroup, ...]:
    return tuple(
        OptionGroup(
            name=name,
            choices={choice: _load_parts(parts) for choice, parts in choices.items()},
        )
        for name, choices in raw.items()
    )


def _load_variant(name: str, spec: dict[str, Any]) -> Variant:
    return Variant(
        name=name,
        parts=_load_parts(spec["parts"]),
        absent=frozenset(r.upper() for r in spec.get("absent", ())),
        options=_load_options(spec.get("options", {})),
    )


def load_golden(path: Path, samples_dir: Path = SAMPLES_DIR) -> GoldenDoc:
    """Loads one golden file.

    A file holds either ``parts`` (one circuit) or ``variants`` (a mapping of
    variant name to ``{"parts": ..., "absent": [...], "options": ...}``).
    ``options`` maps a group name to its choices, each a parts mapping.

    Args:
        path: Path to a golden ``*.json`` file.
        samples_dir: Directory the file's ``pdf`` path is relative to.

    Returns:
        The parsed GoldenDoc.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "variants" in raw:
        variants = tuple(
            _load_variant(name, spec) for name, spec in raw["variants"].items()
        )
    else:
        variants = (_load_variant(DEFAULT_VARIANT, raw),)
    return GoldenDoc(
        name=path.stem,
        pdf=samples_dir / raw["pdf"],
        title=raw["title"],
        variants=variants,
    )


def golden_docs(
    golden_dir: Path = GOLDEN_DIR, samples_dir: Path = SAMPLES_DIR
) -> list[GoldenDoc]:
    """Loads every golden file in a directory, sorted by name."""
    return [
        load_golden(p, samples_dir)
        for p in sorted(golden_dir.glob("*.json"))
        if p.name != BASELINE_PATH.name
    ]


def _grade(expected: ExpectedPart, actual: ActualPart) -> Outcome:
    if actual.category != expected.category:
        return Outcome.WRONG_CATEGORY
    candidates = (expected.value, *expected.accept)
    if not any(values_match(expected.category, c, actual.value) for c in candidates):
        return Outcome.WRONG_VALUE
    if actual.count != expected.count:
        return Outcome.WRONG_COUNT
    return Outcome.CORRECT


def _actual_parts(inventory: Inventory) -> dict[str, ActualPart]:
    actual: dict[str, ActualPart] = {}
    for key, data in inventory.items():
        if key.category == ComponentCategory.PCB:
            continue
        for ref in data["refs"]:
            if "(Inj)" in ref:
                continue
            seen = actual.get(ref.upper())
            # A designator bought twice (two boards, or one table read twice)
            # is graded on its count as well as its value.
            actual[ref.upper()] = ActualPart(
                category=key.category,
                value=key.value,
                count=1 if seen is None else seen.count + 1,
            )
    return actual


def _pair_builds(
    golden: tuple[Variant, ...], builds: list[dict[str, ActualPart]], flagged: set[str]
) -> tuple[list[tuple[Variant, dict[str, ActualPart]]], list[dict[str, ActualPart]]]:
    """Pairs each golden variant with the parser build it agrees with most.

    Pairing is one-to-one and greedy on the number of correct parts. A golden
    variant left without a build is paired with an empty one (everything
    missing); builds left without a golden variant are returned separately.
    When the parser produced a single build, every golden variant is graded
    against it: that is the list a builder of any variant would receive.
    """
    if len(builds) == 1:
        return [(v, builds[0]) for v in golden], []

    def correct(v: Variant, b: dict[str, ActualPart]) -> int:
        return sum(
            1 for r in _grade_variant(v, b, flagged) if r.outcome == Outcome.CORRECT
        )

    # Most correct parts first; ties go to document order.
    scored = sorted(
        (
            (-correct(v, b), gi, bi)
            for gi, v in enumerate(golden)
            for bi, b in enumerate(builds)
        )
    )
    golden_to_build: dict[int, int] = {}
    used: set[int] = set()
    for _, gi, bi in scored:
        if gi not in golden_to_build and bi not in used:
            golden_to_build[gi] = bi
            used.add(bi)
    pairs = [
        (v, builds[golden_to_build[gi]] if gi in golden_to_build else {})
        for gi, v in enumerate(golden)
    ]
    return pairs, [b for bi, b in enumerate(builds) if bi not in used]


@functools.cache
def _parse_cached(pdf: Path) -> ParseResult:
    return parse_pedalpcb_pdf(str(pdf), source_name="Sample")


def parse_sample(pdf: Path) -> ParseResult:
    """Parses a sample PDF once per process, shared by every test that reads it.

    The snapshot and accuracy suites both parse each sample; caching halves that
    work. Callers must treat the result as read-only.

    Args:
        pdf: Path to the sample PDF.

    Returns:
        The parser output for the document.
    """
    return _parse_cached(pdf.resolve())


def sample_group(samples_dir: Path | str, pdf: Path | str) -> str:
    """Names the pytest-xdist group for a sample, so tests sharing its parse share a worker.

    Args:
        samples_dir: The samples root.
        pdf: The sample PDF, absolute or relative to `samples_dir`.

    Returns:
        The sample's path relative to `samples_dir`, in POSIX form.
    """
    return (
        (Path(samples_dir) / pdf)
        .resolve()
        .relative_to(Path(samples_dir).resolve())
        .as_posix()
    )


_REPEAT = re.compile(r"^(.*) \((\d+)\)$")
# Stands for every designator: a document-level warning covers them all.
_EVERYTHING = "*"
# A parts list printed as an image was named: every part missing from the
# reading is covered, but not a wrong value read from the text.
_MISSING = "+"


def _is_flagged(ref: str, flagged: set[str]) -> bool:
    """Whether a warning covers this designator.

    A designator listed twice is keyed 'R1 (2)' for its second listing; the
    parser's warning about the repeat names 'R1' and covers both.
    """
    repeat = _REPEAT.match(ref)
    return (
        _EVERYTHING in flagged
        or ref in flagged
        or (repeat is not None and repeat.group(1) in flagged)
    )


def _with_ocr(pdf: Path, res: ParseResult) -> tuple[ParseResult, OcrAttempt | None]:
    """The parse with OCR parts accepted, as a builder accepting them would get."""
    attempt = attempt_ocr(str(pdf), res, "Sample")
    if attempt is None or not attempt.has_parts:
        return res, attempt
    return replace(res, ocr=attempt).accept_ocr(), attempt


def score_doc(doc: GoldenDoc, ocr: bool = False) -> DocScore:
    """Runs the parser on one document and grades every designator of every build.

    For a multi-circuit document each golden variant is graded against the
    parser build it corresponds to (see `_pair_builds`), so the score says
    whether a builder of *each* variant would get the right parts.

    Args:
        doc: The ground truth to grade against.
        ocr: Attempt OCR where the parse looks incomplete, and accept its parts.

    Returns:
        A DocScore covering every expected and every emitted designator.
    """
    res = parse_sample(doc.pdf)
    attempt = None
    if ocr:
        res, attempt = _with_ocr(doc.pdf, res)
    flagged = {w.ref.upper() for w in res.stats["warnings"] if w.ref}
    if res.stats["errors"] or any(
        w.kind == ParseWarningKind.NO_PARTS_LIST for w in res.stats["warnings"]
    ):
        # The builder was told nothing could be read (or the parse failed, which
        # the app reports): every part is flagged.
        flagged.add(_EVERYTHING)
    if any(
        w.kind == ParseWarningKind.IMAGE_PARTS_LIST and not w.ref
        for w in res.stats["warnings"]
    ):
        flagged.add(_MISSING)

    builds = [_actual_parts(v.inventory) for v in res.variants] or [
        _actual_parts(res.inventory)
    ]
    pairs, unmatched = _pair_builds(doc.variants, builds, flagged)
    labeled = len(doc.variants) > 1 or len(builds) > 1

    results: list[RefResult] = []
    for variant, actual in pairs:
        label = variant.name if labeled else ""
        results += [
            replace(r, build=label) for r in _grade_variant(variant, actual, flagged)
        ]
    for k, actual in enumerate(unmatched, start=1):
        results += [
            RefResult(
                ref,
                Outcome.EXTRA,
                None,
                act,
                _is_flagged(ref, flagged),
                f"extra build {k}",
            )
            for ref, act in actual.items()
        ]

    return DocScore(
        name=doc.name,
        results=tuple(results),
        title_expected=doc.title,
        title_actual=res.title,
        warned_refs=frozenset(flagged - {_EVERYTHING, _MISSING}),
        ambiguous_refs=doc.ambiguous_refs,
        variant_count=len(doc.variants),
        builds_found=len(res.variants) or 1,
        ocr_verdict=attempt.verdict if attempt else None,
        ocr_refs=frozenset(w.ref.upper() for w in attempt.warnings)
        if attempt
        else frozenset(),
    )


def _resolve_options(
    variant: Variant, actual: dict[str, ActualPart]
) -> dict[str, ExpectedPart]:
    """Expected parts with each option group resolved to the choice the output matches.

    Ties go to the first (default) choice. Parts from unchosen choices are not
    expected, so emitting them as well is graded as extra.
    """
    expected = dict(variant.parts)
    for group in variant.options:

        def matched(parts: dict[str, ExpectedPart]) -> int:
            return sum(
                1
                for ref, exp in parts.items()
                if ref in actual and _grade(exp, actual[ref]) == Outcome.CORRECT
            )

        best = max(group.choices.values(), key=matched)  # max keeps the first on ties
        expected |= best
    return expected


def _grade_variant(
    variant: Variant, actual: dict[str, ActualPart], flagged: set[str]
) -> tuple[RefResult, ...]:
    expected = _resolve_options(variant, actual)
    results: list[RefResult] = []
    for ref, exp in expected.items():
        act = actual.get(ref)
        outcome = Outcome.MISSING if act is None else _grade(exp, act)
        covered = _is_flagged(ref, flagged) or (
            outcome == Outcome.MISSING and _MISSING in flagged
        )
        results.append(RefResult(ref, outcome, exp, act, covered))
    for ref, act in actual.items():
        if ref not in expected:
            results.append(
                RefResult(ref, Outcome.EXTRA, None, act, _is_flagged(ref, flagged))
            )
    return tuple(results)


class BaselineEntry(TypedDict):
    """One accepted failure in ``baseline.json``."""

    issue: str
    warned: bool


Baseline = dict[str, dict[str, BaselineEntry]]


def failure_map(score: DocScore) -> dict[str, BaselineEntry]:
    """Maps each failing designator to its description and warning status."""
    return {r.key: {"issue": r.describe(), "warned": r.flagged} for r in score.failures}


def compare_to_baseline(score: DocScore, baseline: Baseline) -> list[str]:
    """Lists every way a document's score differs from the accepted baseline.

    Regressions (new failures, failures that stopped being warned) and
    improvements (fixed failures, newly warned failures) are both reported, so
    the baseline can only move when someone deliberately re-records it.

    Args:
        score: The current score for one document.
        baseline: The accepted baseline for all documents.

    Returns:
        Human-readable differences; empty when the score matches the baseline.
    """
    accepted = baseline.get(score.name, {})
    current = failure_map(score)
    diffs: list[str] = []
    for ref, entry in sorted(current.items()):
        old = accepted.get(ref)
        if old is None:
            diffs.append(f"REGRESSION {ref}: {entry['issue']}")
        elif old["warned"] and not entry["warned"]:
            diffs.append(f"REGRESSION {ref}: no longer warned ({entry['issue']})")
        elif entry["warned"] and not old["warned"]:
            diffs.append(f"improved {ref}: now warned ({entry['issue']})")
    for ref in sorted(accepted.keys() - current.keys()):
        diffs.append(f"improved {ref}: fixed (was {accepted[ref]['issue']})")
    for alarm in sorted(score.false_alarms):
        diffs.append(f"REGRESSION {alarm}: warned about a correctly parsed part")
    return diffs


def render_scorecard(scores: list[DocScore], verbose: bool) -> str:
    """Formats a plain-text scorecard for a list of document scores."""
    lines: list[str] = []
    header = f"{'document':<38}{'correct':>12}{'recall':>9}{'precision':>11}{'silent':>8}{'false alarms':>14}  title"
    lines.append(header)
    lines.append("-" * len(header))
    tot_correct = tot_expected = tot_predicted = tot_silent = tot_false = 0
    for s in scores:
        recall = s.correct / s.expected_count if s.expected_count else 1.0
        precision = s.correct / s.predicted_count if s.predicted_count else 1.0
        tot_correct += s.correct
        tot_expected += s.expected_count
        tot_predicted += s.predicted_count
        tot_silent += len(s.silent_failures)
        tot_false += len(s.false_alarms)
        lines.append(
            f"{s.name:<38}{f'{s.correct}/{s.expected_count}':>12}"
            f"{recall:>9.1%}{precision:>11.1%}{len(s.silent_failures):>8}"
            f"{len(s.false_alarms):>14}  "
            f"{'ok' if s.title_ok else f'got {s.title_actual!r}'}"
            + (
                f"  [{s.variant_count} builds, parser found {s.builds_found}]"
                if s.variant_count > 1 or s.builds_found > 1
                else ""
            )
            + (f"  [OCR: {s.ocr_verdict}]" if s.ocr_verdict else "")
        )
        if verbose:
            for r in s.failures:
                mark = "warned" if r.flagged else "SILENT"
                lines.append(f"    [{mark:>6}] {r.key:<12} {r.describe()}")
            for ref in sorted(s.false_alarms):
                lines.append(f"    [ false] {ref:<12} warned, but parsed correctly")
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<38}{f'{tot_correct}/{tot_expected}':>12}"
        f"{tot_correct / tot_expected:>9.1%}{tot_correct / tot_predicted:>11.1%}"
        f"{tot_silent:>8}{tot_false:>14}"
    )
    return "\n".join(lines)


def write_baseline(scores: list[DocScore]) -> None:
    """Records the current set of failures as the accepted baseline."""
    data = {s.name: failure_map(s) for s in scores}
    BASELINE_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_baseline() -> Baseline:
    """Loads the accepted failure baseline."""
    data: Baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: prints the scorecard and optionally updates the baseline."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="list every failure"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=f"write current failures to {BASELINE_PATH.relative_to(TESTS_DIR.parent)}",
    )
    parser.add_argument(
        "--golden", type=Path, default=GOLDEN_DIR, help="golden file directory"
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=SAMPLES_DIR,
        help="directory the golden files' pdf paths are relative to",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="score only documents OCR reads, with its parts accepted",
    )
    parser.add_argument(
        "docs", nargs="*", help="limit to golden files containing these substrings"
    )
    args = parser.parse_args(argv)

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    docs = golden_docs(args.golden, args.samples)
    if args.docs:
        docs = [d for d in docs if any(s.lower() in d.name.lower() for s in args.docs)]
    scores = [score_doc(d, ocr=args.ocr) for d in docs]
    if args.ocr:
        scores = [s for s in scores if s.ocr_verdict is not None]
    print(render_scorecard(scores, verbose=args.verbose))

    if args.update_baseline:
        if args.docs or args.ocr or args.golden != GOLDEN_DIR:
            print(
                "refusing to update baseline from a filtered or external run",
                file=sys.stderr,
            )
            return 1
        write_baseline(scores)
        print(f"\nbaseline written to {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
