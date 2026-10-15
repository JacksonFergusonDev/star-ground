"""Unit tests for golden-file loading and grading in `tests/accuracy.py`."""

import json
from pathlib import Path
from typing import Any

from src.bom_lib import ComponentCategory
from tests.accuracy import (
    ActualPart,
    Outcome,
    _grade_variant,
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
