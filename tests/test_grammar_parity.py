"""Differential test harness comparing Regex vs Context-Free Grammar parsing.

Validates 100% parity on standard component values from preset data, while
proving that the grammar eliminates regex greedy false-positives and correctly
parses BS 1852 inline notation and unit-suffixed values.
"""

import re
from decimal import Decimal

import pytest

from src.bom_lib.grammar import parse_value_to_decimal as grammar_parse
from src.bom_lib.presets import BOM_PRESETS
from src.bom_lib.utils import parse_value_to_decimal as regex_parse


def _collect_preset_values() -> set[str]:
    """Extracts all standard passive component values from the preset database."""
    values: set[str] = set()
    for preset_data in BOM_PRESETS.values():
        bom_text = preset_data.get("bom_text", "")
        for line in bom_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            designator, value_part = parts[0], parts[1]
            if re.match(r"^[RCL]\d+$", designator):
                first_token = value_part.split()[0]
                if not first_token.endswith("mm") and first_token != "Jumper":
                    values.add(first_token)
    return values


PRESET_STANDARD_VALUES = sorted(_collect_preset_values())


@pytest.mark.parametrize("val_str", PRESET_STANDARD_VALUES)
def test_preset_values_parity(val_str: str) -> None:
    """Verifies that CFG and Regex produce identical Decimal values on all standard presets."""
    regex_result = regex_parse(val_str)
    grammar_result = grammar_parse(val_str)

    assert regex_result is not None, f"Regex failed on standard preset value: {val_str}"
    assert grammar_result is not None, (
        f"Grammar failed on standard preset value: {val_str}"
    )
    assert grammar_result == regex_result, (
        f"Mismatch for '{val_str}': Grammar={grammar_result}, Regex={regex_result}"
    )


@pytest.mark.parametrize(
    ("part_number", "regex_corrupted_value"),
    [
        ("1N4001", Decimal("1")),
        ("1N4148", Decimal("1")),
        ("1N5817", Decimal("1")),
        ("2N3904", Decimal("2")),
        ("2N5088", Decimal("2")),
        ("2N5089", Decimal("2")),
        ("5mm", Decimal("0.005")),
    ],
)
def test_grammar_rejects_regex_greedy_false_positives(
    part_number: str, regex_corrupted_value: Decimal
) -> None:
    """Proves CFG avoids greedy false-positive matching on part numbers and dimensions."""
    # The legacy regex greedily matched leading numbers in part numbers/dimensions
    assert regex_parse(part_number) == regex_corrupted_value

    # The new CFG grammar strictly rejects these non-value tokens
    assert grammar_parse(part_number) is None


@pytest.mark.parametrize(
    ("val_str", "expected"),
    [
        # Ungula: BS 1852 notation
        ("4K7", Decimal("4700")),
        ("1k5", Decimal("1500")),
        ("4n7", Decimal("4.7e-9")),
        ("2M2", Decimal("2200000")),
        ("4u7", Decimal("4.7e-6")),
        ("2p2", Decimal("2.2e-12")),
        ("4R7", Decimal("4.7")),
        ("0R1", Decimal("0.1")),
        # Distortr / Raincoat: Omega symbol & text
        ("330Ω", Decimal("330")),
        ("82Ω", Decimal("82")),
        ("220Ω", Decimal("220")),
        ("390Ω", Decimal("390")),
        ("100ohm", Decimal("100")),
        ("100ohms", Decimal("100")),
        # Pythagoras: MLCC capacitor units
        ("1uF", Decimal("1e-6")),
        ("100nF", Decimal("1e-7")),
        ("47pF", Decimal("4.7e-11")),
        ("10uF", Decimal("1e-5")),
    ],
)
def test_failing_samples_gaps_resolved_by_grammar(
    val_str: str, expected: Decimal
) -> None:
    """Verifies that problem lines and notations from failing samples parse correctly."""
    assert grammar_parse(val_str) == expected
