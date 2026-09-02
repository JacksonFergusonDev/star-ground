"""Unit tests for sourcing resolution, origin assignment, and master shopping list assembly."""

from decimal import Decimal

from src.bom_lib.constants import AUTO_INJECT_SOURCE
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.sourcing import (
    build_shopping_list,
    determine_origin,
    is_extra_part,
    is_pure_hardware,
    resolve_part_sourcing,
)
from src.bom_lib.types import Inventory, ResolvedPartSourcing
from src.bom_lib.units import ureg


def test_is_pure_hardware() -> None:
    """Detects when an item originates solely from hardware auto-injection."""
    assert is_pure_hardware({AUTO_INJECT_SOURCE: ["HW"]}) is True
    assert is_pure_hardware({AUTO_INJECT_SOURCE: ["HW"], "Project A": ["R1"]}) is False
    assert is_pure_hardware({"Project A": ["R1"]}) is False


def test_is_extra_part() -> None:
    """Identifies sockets and adapters as extra add-on hardware."""
    assert is_extra_part("8 PIN DIP SOCKET") is True
    assert is_extra_part("SMD_ADAPTER_BOARD") is True
    assert is_extra_part("10k") is False
    assert is_extra_part("1590B Enclosure") is False


def test_determine_origin() -> None:
    """Classifies component origin appropriately."""
    # Pure hardware
    assert (
        determine_origin("1590B Enclosure", {AUTO_INJECT_SOURCE: ["HW"]})
        == "Hardware Kit"
    )

    # Extras
    assert determine_origin("8 PIN DIP SOCKET", {"Project A": ["U1 (Inj)"]}) == "Extras"

    # Circuit board components
    assert determine_origin("10k", {"Project A": ["R1"]}) == "Circuit Board"


def test_resolve_part_sourcing_circuit_part() -> None:
    """Resolves standard circuit board component."""
    val_qty = Decimal("10000") * ureg.ohm
    resolved = resolve_part_sourcing(
        category=ComponentCategory.RESISTORS,
        val="10k",
        net_qty=4,
        sources={"Project A": ["R1", "R2", "R3", "R4"]},
        val_qty=val_qty,
    )

    assert isinstance(resolved, ResolvedPartSourcing)
    assert resolved.origin == "Circuit Board"
    assert resolved.buy_qty == 10  # 4 rounded up with buffer
    assert resolved.search_term == "10k ohm 1/4w metal film"
    assert "https://www.taydaelectronics.com" in resolved.supplier_url


def test_resolve_part_sourcing_pcb_url_routing() -> None:
    """Routes PCB URLs to PedalPCB when sourced from PedalPCB and not Tayda."""
    pedalpcb_resolved = resolve_part_sourcing(
        category=ComponentCategory.PCB,
        val="Triangulum Boost PCB",
        net_qty=1,
        sources={"PedalPCB Triangulum": ["PCB"]},
    )
    assert "https://www.pedalpcb.com" in pedalpcb_resolved.supplier_url

    tayda_resolved = resolve_part_sourcing(
        category=ComponentCategory.PCB,
        val="Distortion PCB",
        net_qty=1,
        sources={"Tayda Electronics": ["PCB"]},
    )
    assert "https://www.taydaelectronics.com" in tayda_resolved.supplier_url


def test_resolve_part_sourcing_auto_inject_notes() -> None:
    """Appends auto-inject notes for standard parts contributing to circuit board items."""
    resolved = resolve_part_sourcing(
        category=ComponentCategory.RESISTORS,
        val="3.3k",
        net_qty=3,
        sources={"Big Muff": ["R1", "R2"], AUTO_INJECT_SOURCE: ["LED Resistor"]},
    )
    assert "🤖 Standard Part: LED Resistor" in resolved.notes


def test_build_shopping_list_gross_and_net() -> None:
    """Builds shopping list correctly calculating gross vs net needs against stock."""
    inventory = Inventory()
    inventory.add_part("Project A", "Resistors | 10k", "R1", qty=5)
    inventory.add_part("Project A", "Capacitors | 100n", "C1", qty=2)

    stock = Inventory()
    stock.add_part(
        "User Stock", "Resistors | 10k", "", qty=3
    )  # 5 needed, 3 owned -> net 2

    shopping_list = build_shopping_list(inventory, stock=stock)
    assert len(shopping_list) == 2

    res_row = next(r for r in shopping_list if r["Part"] == "10k")
    assert res_row["BOM Qty"] == 5
    assert res_row["In Stock"] == 3
    assert res_row["Net Need"] == 2
    assert res_row["Buy Qty"] >= 2

    cap_row = next(r for r in shopping_list if r["Part"] == "100n")
    assert cap_row["BOM Qty"] == 2
    assert cap_row["In Stock"] == 0
    assert cap_row["Net Need"] == 2


def test_build_shopping_list_filtering() -> None:
    """Filters hardware kits and extras according to boolean flags."""
    inventory = Inventory()
    inventory.add_part("Project A", "Resistors | 10k", "R1", qty=1)
    inventory.add_part(
        AUTO_INJECT_SOURCE, "Hardware/Misc | 1590B Enclosure", "HW", qty=1
    )
    inventory.add_part(
        "Project A", "Hardware/Misc | 8 PIN DIP SOCKET", "U1 (Inj)", qty=1
    )

    # All included
    all_rows = build_shopping_list(inventory, show_hardware=True, show_extras=True)
    assert len(all_rows) == 3

    # Hide hardware
    no_hw_rows = build_shopping_list(inventory, show_hardware=False, show_extras=True)
    assert len(no_hw_rows) == 2
    assert not any(r["Origin"] == "Hardware Kit" for r in no_hw_rows)

    # Hide extras
    no_extras_rows = build_shopping_list(
        inventory, show_hardware=True, show_extras=False
    )
    assert len(no_extras_rows) == 2
    assert not any(r["Origin"] == "Extras" for r in no_extras_rows)

    # Hide both
    circuit_only = build_shopping_list(
        inventory, show_hardware=False, show_extras=False
    )
    assert len(circuit_only) == 1
    assert circuit_only[0]["Part"] == "10k"
