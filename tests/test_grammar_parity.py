"""Verification test harness for Context-Free Grammar (CFG) value parsing.

Validates that standard component values from preset data parse to valid Decimals,
verifies that the grammar strictly rejects non-value tokens (part numbers, dimensions),
and verifies correct handling of BS 1852 notation and unit-suffixed values.
"""

import re
from decimal import Decimal

import pytest

from src.bom_lib.grammar import parse_value_to_decimal as grammar_parse
from src.bom_lib.presets import BOM_PRESETS


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
def test_preset_values_parsing(val_str: str) -> None:
    """Verifies that CFG correctly parses all standard passive values from presets."""
    grammar_result = grammar_parse(val_str)

    assert grammar_result is not None, (
        f"Grammar failed to parse standard preset value: '{val_str}'"
    )
    assert isinstance(grammar_result, Decimal)


@pytest.mark.parametrize(
    "token",
    [
        "1N4001",
        "1N4148",
        "1N5817",
        "2N3904",
        "2N5088",
        "2N5089",
        "5mm",
    ],
)
def test_grammar_rejects_non_value_tokens(token: str) -> None:
    """Proves CFG strictly rejects non-value tokens such as part numbers and dimensions."""
    assert grammar_parse(token) is None


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
