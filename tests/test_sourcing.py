"""Unit tests for sourcing resolution, origin assignment, and master shopping list assembly."""

import pytest

from star_ground.api import MasterList, Project, generate_master_list
from star_ground.bom_lib.constants import IC_SOCKET
from star_ground.bom_lib.enums import ComponentCategory, ComponentOrigin
from star_ground.bom_lib.manager import sort_inventory
from star_ground.bom_lib.sourcing import (
    build_shopping_list,
    determine_origin,
    generate_search_term,
    get_buy_details,
    is_extra_part,
    is_pure_hardware,
    resolve_part_sourcing,
    with_standard_hardware,
)
from star_ground.bom_lib.stages import BuildStage, build_stage
from star_ground.bom_lib.types import (
    BuildHardware,
    ComponentKey,
    Inventory,
    PartData,
    ResolvedPartSourcing,
    ShoppingLine,
)

HARDWARE = ComponentCategory.HARDWARE_MISC


def _hardware(note: str = "Verify PCB fit", qty: int = 1) -> PartData:
    """A part only the hardware kit adds."""
    return PartData(qty=qty, hardware=[note])


def _listed(source: str, *refs: str) -> PartData:
    """A part one project lists under the given designators."""
    data = PartData(qty=len(refs))
    data.refs.extend(refs)
    data.sources[source].extend(refs)
    return data


def test_is_pure_hardware() -> None:
    """Detects when an item comes only from the hardware kit."""
    assert is_pure_hardware(_hardware()) is True
    mixed = _listed("Project A", "R1")
    mixed.hardware.append("LED CLR")
    assert is_pure_hardware(mixed) is False
    assert is_pure_hardware(_listed("Project A", "R1")) is False


def test_is_extra_part() -> None:
    """Only the socket Star Ground injects is an extra."""
    assert is_extra_part(IC_SOCKET) is True
    assert is_extra_part(ComponentKey(ComponentCategory.RESISTORS, "10k")) is False
    assert is_extra_part(ComponentKey(HARDWARE, "1590B Enclosure")) is False


def test_determine_origin() -> None:
    """Classifies component origin appropriately."""
    enclosure = ComponentKey(HARDWARE, "1590B Enclosure")
    assert determine_origin(enclosure, _hardware()) == ComponentOrigin.HARDWARE_KIT

    socket = _listed("Project A", "U1 (Inj)")
    assert determine_origin(IC_SOCKET, socket) == ComponentOrigin.EXTRAS

    resistor = ComponentKey(ComponentCategory.RESISTORS, "10k")
    assert (
        determine_origin(resistor, _listed("Project A", "R1"))
        == ComponentOrigin.CIRCUIT_BOARD
    )


def test_resolve_part_sourcing_circuit_part() -> None:
    """Resolves standard circuit board component."""
    resolved = resolve_part_sourcing(
        ComponentKey(ComponentCategory.RESISTORS, "10k"),
        _listed("Project A", "R1", "R2", "R3", "R4"),
        net_qty=4,
    )

    assert isinstance(resolved, ResolvedPartSourcing)
    assert resolved.origin == ComponentOrigin.CIRCUIT_BOARD
    assert resolved.buy_qty == 10  # 4 rounded up with buffer
    assert resolved.search_term == "10k ohm 1/4w metal film"
    assert "https://www.taydaelectronics.com" in resolved.supplier_url


def test_resolve_part_sourcing_pcb_url_routing() -> None:
    """Searches Tayda for a board, whatever the project is called."""
    resolved = resolve_part_sourcing(
        ComponentKey(ComponentCategory.PCB, "Distortion PCB"),
        _listed("PedalPCB clone", "PCB"),
        net_qty=1,
    )
    assert "https://www.taydaelectronics.com" in resolved.supplier_url


def test_resolve_part_sourcing_hardware_notes() -> None:
    """Notes the standard hardware a circuit board part also covers."""
    data = _listed("Big Muff", "R1", "R2")
    data.hardware.append("LED CLR")
    resolved = resolve_part_sourcing(
        ComponentKey(ComponentCategory.RESISTORS, "3.3k"), data, net_qty=3
    )
    assert "Standard hardware: LED CLR." in resolved.notes


def test_build_shopping_list_gross_and_net() -> None:
    """Builds shopping list correctly calculating gross vs net needs against stock."""
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    key_c = ComponentKey(ComponentCategory.CAPACITORS, "100n")

    inventory = Inventory()
    inventory.add_part("Project A", key_r, "R1", qty=5)
    inventory.add_part("Project A", key_c, "C1", qty=2)

    stock = Inventory()
    stock.add_part("User Stock", key_r, "", qty=3)  # 5 needed, 3 owned -> net 2

    shopping_list = build_shopping_list(inventory, stock=stock)
    assert len(shopping_list) == 2

    res_row = next(r for r in shopping_list if r.part == "10k")
    assert res_row.bom_qty == 5
    assert res_row.in_stock == 3
    assert res_row.net_need == 2
    assert res_row.buy_qty >= 2

    cap_row = next(r for r in shopping_list if r.part == "100n")
    assert cap_row.bom_qty == 2
    assert cap_row.in_stock == 0
    assert cap_row.net_need == 2


def test_build_shopping_list_filtering() -> None:
    """Filters hardware kits and extras according to boolean flags."""
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    key_hw = ComponentKey(HARDWARE, "1590B Enclosure")

    inventory = Inventory()
    inventory.add_part("Project A", key_r, "R1", qty=1)
    inventory[key_hw] = _hardware()
    inventory.add_part("Project A", IC_SOCKET, "U1 (Inj)", qty=1)

    # All included
    all_rows = build_shopping_list(inventory, show_hardware=True, show_extras=True)
    assert len(all_rows) == 3

    # Hide hardware
    no_hw_rows = build_shopping_list(inventory, show_hardware=False, show_extras=True)
    assert len(no_hw_rows) == 2
    assert not any(r.origin == ComponentOrigin.HARDWARE_KIT for r in no_hw_rows)

    # Hide extras
    no_extras_rows = build_shopping_list(
        inventory, show_hardware=True, show_extras=False
    )
    assert len(no_extras_rows) == 2
    assert not any(r.origin == ComponentOrigin.EXTRAS for r in no_extras_rows)

    # Hide both
    circuit_only = build_shopping_list(
        inventory, show_hardware=False, show_extras=False
    )
    assert len(circuit_only) == 1
    assert circuit_only[0].part == "10k"


def test_build_shopping_list_part_included_with_the_pcb() -> None:
    """A part that comes with the PCB stays listed but is not bought."""
    key = ComponentKey(ComponentCategory.ICS, "24LC32A")
    inventory = Inventory()
    inventory.add_part("Deflector", key, "IC3")
    inventory[key].included = 1
    other = Inventory()
    other.add_part("Organ Donor", key, "IC2")
    inventory.merge(other)  # a second project whose chip is bought

    row = next(r for r in build_shopping_list(inventory) if r.part == "24LC32A")
    assert row.bom_qty == 2
    assert row.included == 1
    assert row.net_need == 1
    assert row.buy_qty == 1
    assert row.notes.startswith("1 come with the PCB.")


def test_an_inductor_is_searched_for_by_its_part_type() -> None:
    assert (
        generate_search_term(ComponentCategory.INDUCTORS, "100mH") == "100mH inductor"
    )
    assert generate_search_term(ComponentCategory.RELAYS, "TQ2-L-5V") == "TQ2-L-5V"


def test_a_pot_taper_written_as_a_word_is_searched_for() -> None:
    # Tonepad counts pots as '3 - 100k Log.' and '1 - 500k Rev. Log'.
    pot = ComponentCategory.POTENTIOMETERS
    assert (
        generate_search_term(pot, "100k Log.") == "100k ohm Logarithmic potentiometer"
    )
    assert generate_search_term(pot, "500k Rev. Log") == (
        "500k ohm Reverse Log potentiometer"
    )
    assert generate_search_term(pot, "100kB") == "100k ohm Linear potentiometer"
    assert generate_search_term(pot, "Wah pot") == "Wah pot"


def _master(text: str, name: str = "Pedal") -> MasterList:
    return generate_master_list([Project(name=name, data=text)])


def _row(master: MasterList, part: str, **flags: bool) -> ShoppingLine | None:
    return next((r for r in master.shopping_list(**flags) if r.part == part), None)


@pytest.mark.parametrize(
    ("line", "part"),
    [
        ("J1 2.1MM DC SOCKET", "2.1MM DC SOCKET"),
        ("J1 2.1mm DC Socket", "2.1mm DC Socket"),
        ("V1 9-pin tube socket", "9-pin tube socket"),
    ],
)
def test_a_documents_own_socket_is_no_extra(line: str, part: str) -> None:
    # Only the socket Star Ground adds for a chip is an extra: a DC socket or
    # a tube socket is a part the builder needs, with no spare and no note.
    master = _master(f"{line}\nIC1 TL072")
    row = _row(master, part, show_extras=False)
    assert row is not None
    assert row.origin == ComponentOrigin.CIRCUIT_BOARD
    assert row.buy_qty == 1
    assert row.notes == ""
    assert build_stage(row.category, row.part) == BuildStage.HARDWARE

    socket = _row(master, IC_SOCKET.value)
    assert socket is not None
    assert socket.origin == ComponentOrigin.EXTRAS
    assert socket.buy_qty == 2
    assert socket.notes == "Added by Star Ground: keeps soldering heat off the chip."
    assert socket.search_term == "DIP IC Socket Adaptor Solder Type"
    assert _row(master, IC_SOCKET.value, show_extras=False) is None


def test_chip_sockets_are_soldered_in_the_socket_stage() -> None:
    assert build_stage(IC_SOCKET.category, IC_SOCKET.value) == BuildStage.SOCKETS
    assert build_stage(HARDWARE, "8 pin DIP socket") == BuildStage.SOCKETS
    assert build_stage(HARDWARE, "IC socket") == BuildStage.SOCKETS
    assert build_stage(HARDWARE, "2.1MM DC SOCKET") == BuildStage.HARDWARE


def test_a_project_named_like_the_hardware_kit_keeps_its_parts() -> None:
    # Hardware is known by a field only the kit writes, not by a source label
    # a builder can type.
    master = _master("R1 10k\nIC1 TL072", name="Auto-Inject")
    row = _row(master, "10k", show_hardware=False)
    assert row is not None
    assert row.origin == ComponentOrigin.CIRCUIT_BOARD
    assert _row(master, "TL072", show_hardware=False) is not None

    enclosure = _row(master, "1590B Enclosure")
    assert enclosure is not None
    assert enclosure.origin == ComponentOrigin.HARDWARE_KIT
    assert _row(master, "1590B Enclosure", show_hardware=False) is None


def test_trimmers_take_no_knob_or_seal() -> None:
    master = _master("TRIM1 10k\nTR1 5k\nT1 20k\nVOLUME B100K\nGAIN 10k")
    knob = _row(master, "Knob")
    seal = _row(master, "Dust Seal Cover")
    assert knob is not None
    assert seal is not None
    assert knob.bom_qty == 2  # VOLUME and GAIN
    assert seal.bom_qty == 2

    for part, term in [
        ("5k", "5k ohm trimmer potentiometer"),
        ("20k", "20k ohm trimmer potentiometer"),
        ("B100K", "100k ohm Linear potentiometer"),
    ]:
        row = _row(master, part)
        assert row is not None
        assert row.search_term == term

    # TRIM1 and GAIN are both 10k: one line, which says how many are trimmers.
    shared = _row(master, "10k")
    assert shared is not None
    assert shared.search_term == "10k ohm Linear potentiometer"
    assert "1 of these is a trimmer: search for '10k ohm trimmer potentiometer'." in (
        shared.notes
    )


def test_a_pot_listed_with_a_body_size_first_keeps_its_value() -> None:
    master = _master("GAIN 16mm B10K")
    row = _row(master, "B10K")
    assert row is not None
    assert row.search_term == "10k ohm Linear potentiometer"
    assert generate_search_term(ComponentCategory.POTENTIOMETERS, "16mm B10K") == (
        "10k ohm Linear potentiometer"
    )


def test_only_a_socketed_chip_is_told_to_use_its_socket() -> None:
    master = _master("REG1 78L05\nIC1 TL072\nIC2 BTDR-3H")
    regulator = _row(master, "78L05")
    brick = _row(master, "BTDR-3H")
    chip = _row(master, "TL072")
    assert regulator is not None
    assert brick is not None
    assert chip is not None
    assert regulator.notes == ""
    assert brick.notes == ""
    assert chip.notes.startswith("Fit it in a socket.")


def test_a_counted_regulator_gets_no_socket_note() -> None:
    # Tonepad counts chips by value; the socket is counted for the chip only.
    chip = ComponentKey(ComponentCategory.ICS, "TL072")
    regulator = ComponentKey(ComponentCategory.ICS, "78L05")
    inventory = Inventory()
    inventory.add_counted("Pedal", chip, 1)
    inventory.add_counted("Pedal", IC_SOCKET, 1)
    inventory.add_counted("Pedal", regulator, 1)
    rows = {r.part: r for r in build_shopping_list(inventory)}
    assert rows["TL072"].notes.startswith("Fit it in a socket.")
    assert rows["78L05"].notes == ""


@pytest.mark.parametrize("value", ["JRC4558D", "jrc4558d", "JRC4558DD", "TL072CP"])
def test_a_chip_s_package_letters_keep_its_swaps(value: str) -> None:
    note = get_buy_details(ComponentCategory.ICS, value, 1).note
    assert note.startswith("Swaps to try:")


def test_a_longer_part_number_is_not_another_chip() -> None:
    assert get_buy_details(ComponentCategory.ICS, "LM3080", 1).note == ""


def test_pots_sort_by_resistance_whatever_their_taper_code() -> None:
    inventory = Inventory()
    for value in ["100kB", "10kB", "A250K", "B100K", "B10K", "B1K"]:
        inventory.add_part(
            "Pedal", ComponentKey(ComponentCategory.POTENTIOMETERS, value), "P1"
        )
    assert [k.value for k, _ in sort_inventory(inventory)] == [
        "B1K",
        "10kB",
        "B10K",
        "100kB",
        "B100K",
        "A250K",
    ]


def test_hardware_kit_is_noted_by_purpose() -> None:
    kit = with_standard_hardware(Inventory(), [BuildHardware(1)])
    enclosure = kit[ComponentKey(HARDWARE, "1590B Enclosure")]
    assert enclosure.hardware == ["Verify PCB fit"]
    assert not enclosure.sources
    assert not enclosure.refs
