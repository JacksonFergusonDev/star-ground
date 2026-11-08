"""Verification test harness for Context-Free Grammar (CFG) value parsing.

Validates that standard component values parse to valid Decimals,
verifies that the grammar strictly rejects non-value tokens (part numbers, dimensions),
and verifies correct handling of BS 1852 notation and unit-suffixed values.
"""

from decimal import Decimal

import pytest

from star_ground.bom_lib.grammar import parse_value_to_decimal as grammar_parse

# Every resistor, capacitor and inductor value printed in the parts lists the
# app once shipped as presets (PedalPCB and Tayda kits), as written.
STANDARD_VALUES = [
    "1.2k",
    "1.5K",
    "1.5k",
    "1.5n",
    "1.8k",
    "100",
    "100k",
    "100n",
    "100p",
    "100u",
    "10M",
    "10k",
    "10n",
    "10u",
    "11k",
    "120",
    "12k",
    "150k",
    "150p",
    "15k",
    "15n",
    "15p",
    "16k",
    "18k",
    "18n",
    "1K",
    "1M",
    "1k",
    "1n",
    "1u",
    "2.2M",
    "2.2k",
    "2.2n",
    "2.2u",
    "2.4k",
    "2.7k",
    "20k",
    "220",
    "220k",
    "220n",
    "220p",
    "220u",
    "22M",
    "22k",
    "22n",
    "22u",
    "27k",
    "27n",
    "2M",
    "2k",
    "3.3M",
    "3.3k",
    "3.3n",
    "3.9k",
    "3.9n",
    "30p",
    "330",
    "330k",
    "33k",
    "390",
    "390k",
    "390n",
    "390p",
    "392k",
    "39k",
    "4.7k",
    "4.7n",
    "4.7u",
    "422k",
    "430k",
    "43k",
    "47",
    "470",
    "470k",
    "470n",
    "470p",
    "47k",
    "47n",
    "47p",
    "47u",
    "5.1k",
    "560",
    "560k",
    "56k",
    "6.8k",
    "620k",
    "680k",
    "680p",
    "68k",
    "68n",
    "8.2k",
    "820",
    "820k",
    "820p",
    "82k",
    "82n",
    "9.1k",
]


@pytest.mark.parametrize("val_str", STANDARD_VALUES)
def test_standard_values_parsing(val_str: str) -> None:
    """Verifies that CFG correctly parses standard passive values."""
    grammar_result = grammar_parse(val_str)

    assert grammar_result is not None, (
        f"Grammar failed to parse standard value: '{val_str}'"
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
