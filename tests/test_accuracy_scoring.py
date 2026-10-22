"""Unit tests for golden-file loading and grading in `tests/accuracy.py`."""

import json
from pathlib import Path
from typing import Any

from src.bom_lib import ComponentCategory
from tests.accuracy import (
    ActualPart,
    Outcome,
    _grade_variant,
    _pair_builds,
    load_golden,
    values_match,
)

R, D, Q = (
    ComponentCategory.RESISTORS,
    ComponentCategory.DIODES,
    ComponentCategory.TRANSISTORS,
)


def _part(category: ComponentCategory, value: str) -> dict[str, str]:
    return {"category": category.value, "value": value}


def _write(tmp_path: Path, body: dict[str, Any]) -> Path:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"pdf": "doc.pdf", "title": "Doc", **body}))
    return path


def _outcomes(results) -> dict[str, Outcome]:
    return {r.ref: r.outcome for r in results}


class TestOptions:
    """Option groups: interchangeable choices that may use different designators."""

    def _doc(self, tmp_path: Path):
        return load_golden(
            _write(
                tmp_path,
                {
                    "parts": {"R1": _part(R, "10K")},
                    "options": {
                        "D10": {
                            "Bridge rectifier": {"D10": _part(D, "DI1510")},
                            "Discrete diodes": {
                                f"D10{c}": _part(D, "1N4007") for c in "abcd"
                            },
                        }
                    },
                },
            ),
            tmp_path,
        )

    def test_either_choice_is_correct(self, tmp_path: Path) -> None:
        (variant,) = self._doc(tmp_path).variants
        bridge = {"R1": ActualPart(R, "10k"), "D10": ActualPart(D, "DI1510")}
        discrete = {"R1": ActualPart(R, "10k")} | {
            f"D10{c}": ActualPart(D, "1N4007") for c in "ABCD"
        }
        for actual in (bridge, discrete):
            outcomes = _outcomes(_grade_variant(variant, actual, set()))
            assert set(outcomes.values()) == {Outcome.CORRECT}

    def test_emitting_both_choices_is_extra(self, tmp_path: Path) -> None:
        (variant,) = self._doc(tmp_path).variants
        both = {
            "R1": ActualPart(R, "10k"),
            "D10": ActualPart(D, "DI1510"),
            "D10A": ActualPart(D, "1N4007"),
        }
        outcomes = _outcomes(_grade_variant(variant, both, set()))
        assert outcomes["D10"] == Outcome.CORRECT
        assert outcomes["D10A"] == Outcome.EXTRA

    def test_missing_option_is_graded_against_the_default(self, tmp_path: Path) -> None:
        (variant,) = self._doc(tmp_path).variants
        outcomes = _outcomes(
            _grade_variant(variant, {"R1": ActualPart(R, "10K")}, set())
        )
        assert outcomes == {"R1": Outcome.CORRECT, "D10": Outcome.MISSING}

    def test_option_designators_are_ambiguous(self, tmp_path: Path) -> None:
        assert {"D10", "D10A", "D10D"} <= self._doc(tmp_path).ambiguous_refs


def test_unspecified_values_are_ambiguous(tmp_path: Path) -> None:
    doc = load_golden(
        _write(
            tmp_path,
            {
                "parts": {
                    "R1": {**_part(R, "CLR"), "unspecified": True},
                    "R2": _part(R, "1K"),
                }
            },
        ),
        tmp_path,
    )
    assert doc.ambiguous_refs == {"R1"}


class TestVariants:
    """Variants: separate circuits, each with a complete parts list."""

    def test_designators_that_differ_between_variants_are_ambiguous(
        self, tmp_path: Path
    ) -> None:
        doc = load_golden(
            _write(
                tmp_path,
                {
                    "variants": {
                        "Germanium": {
                            "parts": {"Q1": _part(Q, "1T308A"), "R1": _part(R, "4K7")},
                            "absent": ["R6"],
                        },
                        "Silicon": {
                            "parts": {"Q1": _part(Q, "2N5401"), "R1": _part(R, "4K7")}
                        },
                    }
                },
            ),
            tmp_path,
        )
        assert [v.name for v in doc.variants] == ["Germanium", "Silicon"]
        assert doc.variants[0].absent == {"R6"}
        assert doc.ambiguous_refs == {"Q1"}


def test_values_match_normalizes_notation() -> None:
    assert values_match(R, "4K7", "4.7k")
    assert values_match(ComponentCategory.CAPACITORS, "1uF MLCC", "1u MLCC")
    assert not values_match(ComponentCategory.CAPACITORS, "1u MLCC", "1u")
    assert values_match(ComponentCategory.POTENTIOMETERS, "A100K", "100KA")
    assert values_match(ComponentCategory.SWITCHES, "SPDT (On - On)", "SPDT On/On")


class TestBuildPairing:
    """Each golden variant is graded against the parser build it corresponds to."""

    def _golden(self, tmp_path: Path):
        return load_golden(
            _write(
                tmp_path,
                {
                    "variants": {
                        "Stock": {"parts": {"Q1": _part(Q, "2N3904")}},
                        "Cleft": {"parts": {"Q1": _part(Q, "2N5089")}},
                    }
                },
            ),
            tmp_path,
        ).variants

    def test_builds_pair_regardless_of_order(self, tmp_path: Path) -> None:
        golden = self._golden(tmp_path)
        builds = [{"Q1": ActualPart(Q, "2N5089")}, {"Q1": ActualPart(Q, "2N3904")}]
        pairs, unmatched = _pair_builds(golden, builds, set())
        assert [(v.name, b["Q1"].value) for v, b in pairs] == [
            ("Stock", "2N3904"),
            ("Cleft", "2N5089"),
        ]
        assert unmatched == []

    def test_a_wrong_build_still_counts_against_its_variant(
        self, tmp_path: Path
    ) -> None:
        golden = self._golden(tmp_path)
        stock = {"Q1": ActualPart(Q, "2N3904")}
        wrong = {"R1": ActualPart(R, "10k")}
        pairs, unmatched = _pair_builds(golden, [stock, wrong], set())
        assert {v.name: b for v, b in pairs} == {"Stock": stock, "Cleft": wrong}
        assert unmatched == []

    def test_a_missing_build_is_graded_against_the_closest(
        self, tmp_path: Path
    ) -> None:
        # A build described only in prose (Aion FX legacy Aurora's JangleBox):
        # its builder starts from the offered build it agrees with most.
        golden = self._golden(tmp_path)
        builds = [{"Q1": ActualPart(Q, "2N3904")}, {"Q1": ActualPart(Q, "2N5089")}]
        three = (*golden, golden[0])  # a third golden variant the parser never produced
        pairs, _ = _pair_builds(three, builds, set())
        assert pairs[2][1] == pairs[0][1]

    def test_a_single_build_is_graded_against_every_variant(
        self, tmp_path: Path
    ) -> None:
        golden = self._golden(tmp_path)
        only = {"Q1": ActualPart(Q, "2N3904")}
        pairs, unmatched = _pair_builds(golden, [only], set())
        assert [b for _, b in pairs] == [only, only]
        assert unmatched == []


class TestCountsAndRepeats:
    """Quantities, repeated designators, and document-level warnings."""

    def test_a_designator_bought_twice_is_a_wrong_count(self, tmp_path: Path) -> None:
        # A table read twice would buy every part twice; per-designator grading
        # must see it.
        doc = load_golden(
            _write(tmp_path, {"parts": {"R1": _part(R, "10K")}}), tmp_path
        )
        actual = {"R1": ActualPart(R, "10k", count=2)}
        assert _outcomes(_grade_variant(doc.variants[0], actual, set())) == {
            "R1": Outcome.WRONG_COUNT
        }

    def test_two_boards_declare_their_count(self, tmp_path: Path) -> None:
        body = {"parts": {"Q1": {**_part(Q, "2N5088"), "count": 2}}}
        doc = load_golden(_write(tmp_path, body), tmp_path)
        actual = {"Q1": ActualPart(Q, "2N5088", count=2)}
        assert _outcomes(_grade_variant(doc.variants[0], actual, set())) == {
            "Q1": Outcome.CORRECT
        }

    def test_a_repeat_is_flagged_by_its_base_and_ambiguous(
        self, tmp_path: Path
    ) -> None:
        body = {"parts": {"R1": _part(R, "1M"), "R1 (2)": _part(R, "1k")}}
        doc = load_golden(_write(tmp_path, body), tmp_path)
        results = _grade_variant(doc.variants[0], {"R1": ActualPart(R, "1M")}, {"R1"})
        missing = next(r for r in results if r.ref == "R1 (2)")
        assert missing.outcome == Outcome.MISSING
        assert missing.flagged
        assert {"R1", "R1 (2)"} <= doc.ambiguous_refs

    def test_a_document_level_warning_flags_everything(self, tmp_path: Path) -> None:
        doc = load_golden(_write(tmp_path, {"parts": {"R1": _part(R, "1M")}}), tmp_path)
        (result,) = _grade_variant(doc.variants[0], {}, {"*"})
        assert result.outcome == Outcome.MISSING
        assert result.flagged
