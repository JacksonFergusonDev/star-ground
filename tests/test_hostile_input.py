"""Long, hostile input is read in linear time.

Each case once took several seconds or minutes, because a regex retried a
scan from every position (or a check compared every line with every other).
A pasted list may be 200,000 characters and an upload far more, and a reading
has no time limit, so each could hold a server core. The bound is generous
(each input took 3 s or more before), so the tests aren't flaky on a slow
runner.
"""

import time
from collections.abc import Callable

import pytest

from star_ground.bom_lib import builds, classifier
from star_ground.bom_lib.builds import PageContext, plan_builds
from star_ground.bom_lib.choices import choose_value
from star_ground.bom_lib.enums import ComponentCategory, InputMethod
from star_ground.bom_lib.pdf import bom_table
from star_ground.bom_lib.pdf.cells import CONTROL_VALUE
from star_ground.bom_lib.strategies.context import BOMParserContext
from star_ground.bom_lib.strategies.manual import parse_with_verification
from star_ground.bom_lib.types import ComponentKey, ReadingStats
from star_ground.bom_lib.verification import check_footnotes

_BOUND_S = 1.0


def _seconds(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def test_pasted_line_with_a_long_run_of_spaces() -> None:
    # 15.6 s before; quadrupled per doubling of the spaces.
    line = "R1 x" + " " * 40000 + "x"
    assert _seconds(lambda: parse_with_verification([line])) < _BOUND_S


def test_pasted_line_is_still_read() -> None:
    result = parse_with_verification(["R1, 10k ", "C1\t100n"])
    assert {k.value for k in result.inventory} == {"10k", "100n"}
    assert result.stats.residuals == []


def test_pasted_value_of_many_slashes() -> None:
    # 14.2 s before, through the paste reader to `choose_value`.
    text = "R1 " + "a/" * 20000 + " 1"
    context = BOMParserContext()
    assert (
        _seconds(lambda: context.process(InputMethod.PASTE_TEXT, text, "s")) < _BOUND_S
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a/" * 20000 + " 1", id="slashes"),  # 14 s
        pytest.param("1" * 40000 + "!", id="digits"),  # 30 s
        pytest.param("a" + " " * 40000 + "b", id="spaces"),  # 4 s
        pytest.param("(or a" + " " * 40000 + "b", id="open-alternative"),  # 11 s
        pytest.param("(a" + " " * 40000 + "b", id="open-will-also-work"),
    ],
)
def test_choose_value_on_long_values(value: str) -> None:
    assert _seconds(lambda: choose_value("R1", value)) < _BOUND_S


def test_choose_value_still_reads_choices() -> None:
    assert choose_value("R1", "1M to 2M2").value == "1M"
    assert choose_value("R1", "4u7 or 5uF electro").value == "4u7 electro"
    assert choose_value("R1", "270nF/220nF").value == "270nF"
    assert choose_value("R1", "51pF (or 47pF) MLCC").value == "51pF MLCC"
    assert choose_value("IC1", "4558 (TL072 will also work)").value == "4558"
    assert choose_value("D1", "Silicon diode (1N456, 1N4148, etc)").value == (
        "Silicon diode"
    )


def test_many_dip_sizes_in_one_value() -> None:
    # 6.3 s before: each DIP size rescanned the rest for 'SWITCH'.
    value = "DIP1 " * 20000
    assert _seconds(lambda: classifier.categorize_part("FOO", value)) < _BOUND_S
    assert classifier.DIP_SWITCH.search(value) is None
    assert classifier.DIP_SWITCH.search(value + "SWITCH") is not None
    assert classifier.DIP_SWITCH.search("X DIP8 8 POSITION SWITCH") is not None
    assert classifier.DIP_SWITCH.search("DIP8\nSWITCH") is None


def test_many_either_notes_on_a_page() -> None:
    # 9.2 s before: each 'either' scanned the rest of the page for 'control'.
    page = PageContext([], "either " * 14000 + "x control or y")
    assert _seconds(lambda: plan_builds([], [page])) < _BOUND_S
    assert builds._EITHER_OR.search("either Nature control, or Treble + Bass controls")


def test_many_starred_lines() -> None:
    # 5.5 s before: every '*' heading matched against every line.
    text = "\n".join(f"*a{i}" for i in range(4000))
    assert _seconds(lambda: check_footnotes([text], ReadingStats())) < _BOUND_S


def test_footnote_is_still_found() -> None:
    stats = ReadingStats()
    text = (
        "OSC-ROOT *DPDT On/On/On\nR1 10k\n*DPDT On/On/On\n"
        "This switch MUST be a Type-2 DPDT On/On/On toggle switch."
    )
    check_footnotes([text], stats)
    assert [w.message for w in stats.warnings] == [
        "OSC-ROOT (DPDT On/On/On) has a note: This switch MUST be a Type-2 "
        "DPDT On/On/On toggle switch."
    ]


def test_control_value_on_a_long_number() -> None:
    # 4.7 s per match before, and a cell is matched several times.
    assert _seconds(lambda: CONTROL_VALUE.match("1" * 40000)) < _BOUND_S
    assert CONTROL_VALUE.match('1/4" Switching TRS') is not None


def test_unclosed_aside_after_many_parentheses() -> None:
    # 3.3 s before: each '(' rescanned the rest of the cell for ')'.
    cell = "(" * 40000 + ")"
    assert _seconds(lambda: bom_table._without_unclosed_aside(cell)) < _BOUND_S
    assert bom_table._without_unclosed_aside(cell) == cell
    assert bom_table._without_unclosed_aside("1N5226 (1N5") == "1N5226"
    assert bom_table._without_unclosed_aside("100n (104) (low") == "100n (104)"


def test_key_quantity_cache_is_bounded() -> None:
    # Keyed by values builders send: unbounded, it grows for the server's life.
    assert classifier.key_quantity.cache_info().maxsize is not None
    key = ComponentKey(ComponentCategory.RESISTORS, "10k")
    assert classifier.key_quantity(key) == classifier.key_quantity(key)
