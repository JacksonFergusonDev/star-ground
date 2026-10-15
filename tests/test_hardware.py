"""Standard hardware is supplied only for pedals whose document doesn't list its own."""

from src.bom_lib import (
    BuildHardware,
    ComponentCategory,
    ComponentKey,
    HardwareRole,
    Inventory,
    get_standard_hardware,
    provided_hardware,
)

HW = ComponentCategory.HARDWARE_MISC
INPUT_JACK = ComponentKey(HW, "6.35MM JACK (STEREO)")
DC_JACK = ComponentKey(HW, "DC POWER JACK 2.1MM")
STOMP = ComponentKey(HW, "3PDT STOMP SWITCH")
ENCLOSURE = ComponentKey(HW, "1590B Enclosure")


def _inventory(*parts: tuple[ComponentCategory, str, str]) -> Inventory:
    inventory = Inventory()
    for category, value, ref in parts:
        inventory.add_part("Doc", ComponentKey(category, value), ref)
    return inventory


class TestProvidedHardware:
    """Only explicit mentions count as the document supplying hardware."""

    def test_listed_jacks_and_dc_jack(self) -> None:
        inventory = _inventory(
            (HW, "NMJ6HCD2", "JACK_IN"),
            (HW, "NMJ6HCD2", "JACK_A"),
            (HW, "DC JACK", "JACK_DC"),
        )
        assert provided_hardware(inventory) == {
            HardwareRole.AUDIO_JACKS,
            HardwareRole.DC_JACK,
        }

    def test_latching_3pdt_footswitch(self) -> None:
        inventory = _inventory(
            (ComponentCategory.SWITCHES, "3PDT Latching Footswitch", "BYPASS")
        )
        assert provided_hardware(inventory) == {HardwareRole.FOOTSWITCH}

    def test_momentary_or_toggle_switches_are_not_the_bypass_footswitch(self) -> None:
        inventory = _inventory(
            (ComponentCategory.SWITCHES, "SPST Momentary", "FS"),
            (ComponentCategory.SWITCHES, "SPDT ON/ON", "SW1"),
        )
        assert provided_hardware(inventory) == frozenset()

    def test_ordinary_parts_provide_nothing(self) -> None:
        inventory = _inventory(
            (ComponentCategory.RESISTORS, "10k", "R1"),
            (ComponentCategory.DIODES, "3mm Red LED", "D1"),
        )
        assert provided_hardware(inventory) == frozenset()


class TestInjection:
    """Hardware quantities follow the projects that need them."""

    def test_no_project_lists_hardware(self) -> None:
        inventory = Inventory()
        get_standard_hardware(inventory, [BuildHardware(2), BuildHardware(1)])
        assert inventory[INPUT_JACK]["qty"] == 3
        assert inventory[STOMP]["qty"] == 3

    def test_project_listing_its_own_jacks_is_skipped(self) -> None:
        inventory = Inventory()
        own_jacks = frozenset({HardwareRole.AUDIO_JACKS, HardwareRole.DC_JACK})
        get_standard_hardware(
            inventory, [BuildHardware(2, own_jacks), BuildHardware(1)]
        )
        assert inventory[INPUT_JACK]["qty"] == 1  # only the other project's pedal
        assert inventory[DC_JACK]["qty"] == 1
        assert inventory[STOMP]["qty"] == 3  # nobody listed a footswitch
        assert any(
            "list their own" in note
            for notes in inventory[INPUT_JACK]["sources"].values()
            for note in notes
        )

    def test_hardware_every_project_lists_is_not_added(self) -> None:
        inventory = Inventory()
        get_standard_hardware(
            inventory, [BuildHardware(1, frozenset({HardwareRole.ENCLOSURE}))]
        )
        assert ENCLOSURE not in inventory
