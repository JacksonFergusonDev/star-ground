"""What a chip's part number says about it: its pin count, and whether it's a charge pump.

The shopping list sizes each chip's socket by its pins, and buys electrolytic
capacitors rated for a charge pump's higher voltage. Both are read from the
part number alone, so only families whose package is certain are listed: a
chip that isn't keeps a socket of unknown size.
"""

import re

# DIP pin counts by family. A pattern matches from the start of the part
# number in capitals without spaces ('JRC4558D', 'CD4049UBE', 'NE 5532' is
# 'NE5532'), with an optional maker's prefix; letters after it name the
# package or grade. Digits may not follow, so 'TL0721' is no TL072.
_PIN_FAMILIES: tuple[tuple[int, str], ...] = (
    (
        8,
        r"TL0[678][12]|TL022|(?:JRC|NJM|RC|KA|LM)?45(?:58|59|80)|LM833|NE553[24]"
        r"|JRC5534|OPA2?134|OPA2604|OPA?1678|MC33178|(?:LM|JRC)386|LT1054"
        r"|(?:TC|ICL|MAX)?(?:1044|7660)|LM308|LM301|(?:LM|UA|JRC)?741|OP07"
        r"|LF35[13]|LM353|LF412|24LC\d+|LM[23]58|(?:LM|MC|JRC)?1458|CA3130"
        r"|(?:CA|LM)3080|CA3240|MCP41\d+|MN3[012]0[1-7]|V3[012]0[27]"
        r"|TLC22[67]2|AD712|AD823|MCP602|LM4562|OP275|NE555"
        r"|ATTINY(?:13|25|45|85)A?",
    ),
    (
        14,
        r"TL0[678]4|TLE2074|LM324|LM339|LM2902|LF347|(?:JRC|RC)4741|ICL8038"
        r"|(?:CD|HEF)?40(?:11|13|24|47|66|69|70|71|81|93|106)",
    ),
    (
        16,
        r"(?:CD|HEF)?40(?:17|27|40|42|46|49|50|51|52|53)|(?:SN)?74HC595"
        r"|LM13[67]00|PT2399|XR2206",
    ),
)

_PINS = tuple(
    (pins, re.compile(rf"(?:{families})(?!\d)")) for pins, families in _PIN_FAMILIES
)

# Chips that double or invert the supply: TC1044, ICL7660, MAX1044, LT1054.
_CHARGE_PUMP = re.compile(r"(?:TC|ICL|MAX|LT)?(?:1044|7660|1054)(?!\d)")


def _compact(value: str) -> str:
    """A part number in capitals without spaces: 'ne 5532' -> 'NE5532'."""
    return re.sub(r"\s+", "", value).upper()


def chip_pins(value: str) -> int | None:
    """A chip's DIP pin count, from its part number.

    Args:
        value: The chip's value ('TL072CP', 'JRC4558D', 'PT2399').

    Returns:
        8, 14 or 16, or None for a chip whose family isn't listed.
    """
    compact = _compact(value)
    for pins, family in _PINS:
        if family.match(compact):
            return pins
    return None


def is_charge_pump(value: str) -> bool:
    """Whether a chip doubles or inverts the supply (TC1044, 7660, LT1054).

    A project with one runs some of its capacitors at 18 V or more.

    Args:
        value: The chip's value.

    Returns:
        True for a charge pump.
    """
    return _CHARGE_PUMP.match(_compact(value)) is not None
