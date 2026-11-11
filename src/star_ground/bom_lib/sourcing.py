"""Business logic for sourcing, purchasing, and hardware injection.

This module contains the "Nerd Economics" logic, which includes:
- Calculating safe buy quantities (buffers for small parts).
- Generating search terms for suppliers (e.g., Tayda).
- Injecting required hardware (jacks, switches) that isn't on the PCB BOM.
- Identifying missing or residual parts from parsing.
"""

import copy
import math
import re
from collections.abc import Sequence
from decimal import Decimal
from urllib.parse import quote_plus

from star_ground.bom_lib import constants
from star_ground.bom_lib.classifier import (
    categorize_part,
    key_quantity,
    normalize_value_to_quantity,
)
from star_ground.bom_lib.constants import IC_SOCKET, INJECTED_SUFFIX
from star_ground.bom_lib.designators import parse_designator
from star_ground.bom_lib.enums import (
    ComponentCategory,
    ComponentOrigin,
    ComponentSpec,
    HardwareRole,
)
from star_ground.bom_lib.manager import (
    POT_RESISTANCE,
    calculate_net_needs,
    sort_inventory,
)
from star_ground.bom_lib.types import (
    AlternativeSpec,
    BuildHardware,
    ComponentKey,
    Inventory,
    PartData,
    PurchaseRecommendation,
    ReadingStats,
    ResolvedPartSourcing,
    ShoppingLine,
)
from star_ground.bom_lib.units import Value, farads, ohms
from star_ground.bom_lib.utils import (
    parse_value_to_decimal,
    value_to_search_string,
)


def _format_alts(alts: list[AlternativeSpec]) -> str:
    """Formats an alternatives list as a sentence: 'Swaps to try: ...'."""
    txt_parts = [
        f"{alt.name} ({alt.profile}{': ' + alt.justification if alt.justification else ''})"
        for alt in alts
    ]
    return f"Swaps to try: {', '.join(txt_parts)}."


def get_residual_report(stats: ReadingStats) -> list[str]:
    """Identifies potential parts hidden in the parser's rejected lines.

    Scans the reading's residuals (unparsed lines) for text that looks like a
    part but was missed by the regex. Useful for debugging parsing errors.

    Args:
        stats: The reading whose residuals are scanned.

    Returns:
        A list of suspicious lines that might require manual review.
    """
    safe_words = [
        "RESISTORS",
        "CAPACITORS",
        "TRANSISTORS",
        "DIODES",
        "POTENTIOMETERS",
        "PCB",
        "COMPONENT LIST",
        "SOCKET",
    ]
    suspicious: list[str] = []

    for line in stats.residuals:
        # Pass explicit errors/exceptions through
        if "ERROR" in line.upper() or "EXCEPTION" in line.upper():
            suspicious.append(line)
            continue

        upper = line.upper()
        is_header = any(w in upper for w in safe_words)

        # If it's not a header but has numbers, it might be a missed part
        if not is_header and any(c.isdigit() for c in line):
            suspicious.append(line)

    return suspicious


def get_spec_type(
    category: ComponentCategory,
    val: str,
    val_qty: Value | Decimal | None = None,
) -> ComponentSpec:
    """Determines the specific capacitor dielectric or material type.

    Used to refine search terms (e.g., distinguishing MLCC from Electrolytic
    based on capacitance).

    Args:
        category: Component category.
        val: Component value string.
        val_qty: Optional pre-computed physical quantity or Decimal.

    Returns:
        A ComponentSpec describing the type (e.g., ComponentSpec.MLCC, ComponentSpec.BOX_FILM),
        or ComponentSpec.NONE if not applicable.
    """
    if category == ComponentCategory.CAPACITORS:
        if val_qty is None:
            val_qty = normalize_value_to_quantity(category, val)
        if val_qty is None:
            return ComponentSpec.NONE

        threshold_1n = farads("1e-9")
        threshold_1u = farads("1e-6")

        # < 1nF -> Ceramic/MLCC
        if val_qty < threshold_1n:
            return ComponentSpec.MLCC

        # 1nF to 1uF -> Film (including exact 1uF)
        if threshold_1n <= val_qty <= threshold_1u:
            return ComponentSpec.BOX_FILM

        # > 1uF -> Electrolytic
        return ComponentSpec.ELECTROLYTIC

    return ComponentSpec.NONE


# A taper spelled as a word. Reverse log before log.
_POT_TAPER_WORDS = (
    (re.compile(r"REV(?:ERSE)?\.?\s*LOG"), "Reverse Log"),
    (re.compile(r"\bLOG"), "Logarithmic"),
    (re.compile(r"\bLIN"), "Linear"),
)


def generate_search_term(
    category: ComponentCategory,
    val: str,
    spec_type: ComponentSpec = ComponentSpec.NONE,
    trimmer: bool = False,
) -> str:
    """Generates a supplier-optimized search string.

    Targeted primarily at Tayda Electronics' search engine behavior.

    Args:
        category: Component category.
        val: Component value.
        spec_type: Optional material type (from get_spec_type).
        trimmer: Whether a potentiometer is a board-mounted trimmer (see
            `trimmer_count`), which has no taper or knob.

    Returns:
        A search string (e.g., "100k ohm Linear potentiometer").
    """
    if category == ComponentCategory.RESISTORS:
        return f"{val} ohm 1/4w metal film"

    if category == ComponentCategory.CAPACITORS:
        # Check if it ends in a shorthand unit (p, n, u) and append 'F'
        if val and val[-1] in "pnu":
            val += "F"

        if spec_type == ComponentSpec.MLCC:
            return f"{val} multilayer"
        if spec_type and spec_type != ComponentSpec.NONE:
            return f"{val} {spec_type.value}"
        return val

    if category == ComponentCategory.POTENTIOMETERS:
        val_upper = val.upper()
        is_dual = "DUAL" in val_upper or "STEREO" in val_upper
        resistance = POT_RESISTANCE.search(val_upper)
        dec = parse_value_to_decimal(resistance.group(2)) if resistance else None
        if resistance is None or dec is None:
            return val  # 'Wah pot' names the part itself
        if trimmer:
            return f"{value_to_search_string(ohms(dec))} ohm trimmer potentiometer"
        # A taper is a letter by the value ('B100K', '100kB', '100k-A') or a
        # word ('100k Log.', '500k Rev. Log', Tonepad).
        code = resistance.group(1) or resistance.group(3)
        word = next((t for p, t in _POT_TAPER_WORDS if p.search(val_upper)), None)
        taper = word or constants.POT_TAPER_MAP.get(code or "B", "Linear")
        base_term = f"{value_to_search_string(ohms(dec))} ohm {taper} potentiometer"
        return f"Dual Gang {base_term}" if is_dual else base_term

    if category == ComponentCategory.DIODES:
        if val.upper() == "LED":
            return "LED 3mm"
        return val

    # A bare '100mH' finds nothing; the part type does.
    if category == ComponentCategory.INDUCTORS and "INDUCTOR" not in val.upper():
        return f"{val} inductor"

    # Overrides. The socket's size depends on its chip, so the search lists
    # every size.
    if is_extra_part(ComponentKey(category, val)):
        return "DIP IC Socket Adaptor Solder Type"
    if "JRC4558" in val.upper():
        return "NJM4558D"

    return val


def generate_tayda_url(search_term: str) -> str:
    """Generates a clickable search URL for Tayda Electronics."""
    if not search_term:
        return ""
    encoded = quote_plus(search_term)
    return f"https://www.taydaelectronics.com/catalogsearch/result/?q={encoded}"


def get_buy_details(
    category: ComponentCategory,
    val: str,
    count: int,
    val_qty: Value | Decimal | None = None,
    socketed: bool = False,
) -> PurchaseRecommendation:
    """Calculates the purchase quantity and notes based on 'Nerd Economics'.

    Applies logic to buffer small parts (resistors), enforce exact counts
    for expensive parts (ICs), and suggest substitutions or warnings.

    Args:
        category: Component category.
        val: Component value.
        count: The raw net need (BOM Qty - Stock Qty).
        val_qty: Pre-computed quantity or Decimal value of the component, if available.
        socketed: Whether Star Ground added a socket for a chip (see
            `takes_socket`); a regulator gets none.

    Returns:
        PurchaseRecommendation containing buy_qty and notes.
    """
    if count <= 0:
        return PurchaseRecommendation(buy_qty=0, note="")

    buy = count
    note = ""

    if category == ComponentCategory.RESISTORS:
        resistor_rule = constants.PURCHASING_CONFIG.get(ComponentCategory.RESISTORS)
        assert isinstance(resistor_rule, constants.ResistorPurchasingRule)
        buffered_qty = count + resistor_rule.buffer_add
        round_step = resistor_rule.round_to
        buy = math.ceil(buffered_qty / round_step) * round_step

        note = resistor_rule.note
        if val_qty is not None and val_qty < resistor_rule.suspicious_threshold_low:
            note = "Under 1 Ω: check this value against the document."

    elif category == ComponentCategory.OPTOELECTRONICS:
        buy = count + 1  # Fragile legs

    elif category == ComponentCategory.CAPACITORS:
        cap_rule = constants.PURCHASING_CONFIG.get(ComponentCategory.CAPACITORS)
        assert isinstance(cap_rule, constants.CapacitorPurchasingRule)
        note_parts: list[str] = []
        buffer = cap_rule.standard_buffer

        # Bypass caps (100nF) -> Bulk buy
        if val_qty is not None and val_qty == cap_rule.bulk_threshold:
            buffer = cap_rule.bulk_buffer
            note_parts.append("A common bypass value: worth buying in bulk.")
        # Large caps (>= 1uF) -> Low buffer
        elif val_qty is not None and val_qty >= cap_rule.large_threshold:
            buffer = cap_rule.large_buffer

        buy = count + buffer
        if val_qty is not None and val_qty > cap_rule.suspicious_threshold_high:
            note_parts.append("Over 10 mF: check this value against the document.")

        spec_type = get_spec_type(category, val, val_qty=val_qty)
        if spec_type and spec_type != ComponentSpec.NONE:
            if (
                spec_type == ComponentSpec.BOX_FILM
                and val_qty is not None
                and val_qty == cap_rule.large_threshold
            ):
                note_parts.append(
                    f"{spec_type.value}. The document may mean an electrolytic: check it."
                )
            elif spec_type == ComponentSpec.MLCC:
                note_parts.append("Class 1 ceramic (C0G/NP0).")
            else:
                note_parts.append(f"{spec_type.value}.")
        note = " ".join(note_parts)

    elif category == ComponentCategory.DIODES:
        diode_rule = constants.PURCHASING_CONFIG.get(ComponentCategory.DIODES)
        assert isinstance(diode_rule, constants.DiodePurchasingRule)
        buy = max(diode_rule.min_buy, count + diode_rule.buffer_add)
        # Check substitutions
        if val in constants.DIODE_ALTS:
            alts = constants.DIODE_ALTS[val]
            note = _format_alts(alts)

    elif category == ComponentCategory.TRANSISTORS:
        buy = count + 1
        if "2N5457" in val.upper():
            note = "Obsolete: look for a specialist seller, or use an MMBF5457 on an adapter."
        elif "MMBF" in val.upper():
            note = "Surface-mount: check the board has pads for it, or buy an adapter."

    elif category == ComponentCategory.ICS:
        buy = count
        ic_notes = ["Fit it in a socket."] if socketed else []
        ic_alts = constants.IC_ALTS.get(_base_part_number(val))
        if ic_alts:
            ic_notes.append(_format_alts(ic_alts))
        note = " ".join(ic_notes)

    elif category == ComponentCategory.CRYSTALS_OSCILLATORS:
        buy = count + 1
        note = "Fragile and heat-sensitive."

    elif category == ComponentCategory.HARDWARE_MISC:
        if is_extra_part(ComponentKey(category, val)):
            buy = count + 1
            note = "Added by Star Ground: keeps soldering heat off the chip."
        else:
            buy = count

    elif category == ComponentCategory.PCB:
        note = "Main Board"

    return PurchaseRecommendation(buy_qty=buy, note=note)


# A document's own power connector, whatever it calls it: 'DC JACK',
# '2.1MM DC SOCKET', 'POWER SOCKET', 'DC power jack', 'Barrel connector'. A
# chip's socket names no power. Up to two words may come between 'DC' and
# the connector, which keeps the search linear.
_DC_POWER = re.compile(
    r"\b(?:DC|POWER)(?:[\s_-]+[^\s_-]+){0,2}?[\s_-]+(?:JACK|SOCKET|CONNECTOR)S?\b"
    r"|\bBARREL\b|\b\d(?:\.\d)?\s*MM\s+DC\b"
)


def provided_hardware(inventory: Inventory) -> frozenset[HardwareRole]:
    """Off-board hardware a project's own parts list already includes.

    Only explicit mentions count (a part described as a jack, a DC power
    connector, a 3PDT footswitch, or an enclosure), so standard hardware is
    skipped only when the document clearly supplies it. A DC power connector
    counts whatever it is called (jack, socket, barrel). A status LED and its
    resistor count by their
    designators: 'LED', 'LED1', 'EFL1' (GuitarPCB NostalgiTone) for the LED;
    'CLR' (PedalPCB, GuitarPCB) and 'LEDR' (Aion FX) for the resistor. A
    clipping LED is a numbered diode ('D1  3mm Red LED') and counts as neither.

    Args:
        inventory: One project's parsed inventory (before standard hardware).

    Returns:
        The roles the document covers.
    """
    roles: set[HardwareRole] = set()
    for key, data in inventory.items():
        text = " ".join([key.value, *data.refs]).upper()
        if "ENCLOSURE" in text:
            roles.add(HardwareRole.ENCLOSURE)
        if _DC_POWER.search(text):
            roles.add(HardwareRole.DC_JACK)
        elif "JACK" in text:
            roles.add(HardwareRole.AUDIO_JACKS)
        if (
            key.category == ComponentCategory.SWITCHES
            and "3PDT" in text
            and ("FOOTSWITCH" in text or any(r.startswith("FS") for r in data.refs))
        ):
            roles.add(HardwareRole.FOOTSWITCH)
        refs = [r.upper() for r in data.refs]
        if key.category == ComponentCategory.DIODES and any(
            r.startswith(("LED", "EFL")) for r in refs
        ):
            roles.add(HardwareRole.STATUS_LED)
        if key.category == ComponentCategory.RESISTORS and any(
            r.startswith(("CLR", "RLED", "LED")) for r in refs
        ):
            roles.add(HardwareRole.LED_RESISTOR)
    return frozenset(roles)


def status_leds(inventory: Inventory) -> int:
    """How many status LEDs a project's parts list lights, at least one.

    Each needs a current-limiting resistor; a document that lists the LEDs
    may print their resistors where they are not read ('CLR x3' under GuitarPCB
    Space Modulator's table).

    Args:
        inventory: One project's parsed inventory (before standard hardware).

    Returns:
        The number of status LEDs ('LED', 'LED1', 'EFL1' designators).
    """
    return max(
        1,
        sum(
            r.upper().startswith(("LED", "EFL"))
            for key, data in inventory.items()
            if key.category == ComponentCategory.DIODES
            for r in data.refs
        ),
    )


def with_standard_hardware(
    parts: Inventory, builds: Sequence[BuildHardware]
) -> Inventory:
    """A copy of the parts with standard enclosure hardware added.

    Adds items like jacks, switches, DC sockets, and wiring that are almost
    never listed on the PCB BOM but are required to build the pedal. Hardware a
    project's document already lists (see `provided_hardware`) is supplied
    only for the other projects' pedals, so nothing is bought twice. With no
    pedals, nothing is added.

    Args:
        parts: The master list's parts, left unchanged.
        builds: Each project's pedal count and the hardware it provides.

    Returns:
        The parts with the hardware added.
    """
    inventory = copy.deepcopy(parts)
    pedal_count = sum(b.count for b in builds)
    if pedal_count == 0:
        return inventory  # no pedal to house

    def needing(role: HardwareRole | None) -> int:
        if role is None:
            return pedal_count
        return sum(b.count for b in builds if role not in b.provided)

    def inject(
        category: ComponentCategory,
        val: str,
        qty_per_pedal: int,
        note: str,
        qty_override: int | None = None,
        role: HardwareRole | None = None,
    ) -> None:
        pedals = needing(role)
        if role is not None and pedals == 0:
            return  # every project lists its own
        if role is not None and pedals < pedal_count:
            note = f"{note}; {pedal_count - pedals} pedal(s) list their own"
        key = ComponentKey(category=category, value=val)
        total_qty = (
            qty_override if qty_override is not None else (qty_per_pedal * pedals)
        )

        part = inventory[key]
        part.qty += total_qty
        part.hardware.append(note)

    # 1. Smart Merges (Add to existing categories)
    # One resistor per status LED: a document may list several LEDs.
    inject(
        ComponentCategory.RESISTORS,
        "3.3k",
        1,
        "LED CLR",
        qty_override=sum(
            b.count * b.status_leds
            for b in builds
            if HardwareRole.LED_RESISTOR not in b.provided
        ),
        role=HardwareRole.LED_RESISTOR,
    )
    inject(
        ComponentCategory.DIODES, "LED", 1, "Status Light", role=HardwareRole.STATUS_LED
    )

    # 2. Germanium Heuristic (Fuzz check)
    if any(
        "FUZZ" in k.value.upper()
        for k in inventory
        if k.category == ComponentCategory.PCB
    ):
        inject(ComponentCategory.TRANSISTORS, "Germanium PNP", 0, "Vintage Option")

    # 3. Standard Enclosure Hardware
    enclosure, footswitch = HardwareRole.ENCLOSURE, HardwareRole.FOOTSWITCH
    jacks, dc = HardwareRole.AUDIO_JACKS, HardwareRole.DC_JACK
    inject(
        ComponentCategory.HARDWARE_MISC,
        "1590B Enclosure",
        1,
        "Verify PCB fit",
        role=enclosure,
    )
    inject(
        ComponentCategory.HARDWARE_MISC,
        "3PDT FOOTSWITCH PCB",
        1,
        "Wiring Board",
        role=footswitch,
    )
    inject(
        ComponentCategory.HARDWARE_MISC,
        "3PDT STOMP SWITCH",
        1,
        "Blue/Standard",
        role=footswitch,
    )
    inject(
        ComponentCategory.HARDWARE_MISC, "6.35MM JACK (STEREO)", 1, "Input", role=jacks
    )
    inject(
        ComponentCategory.HARDWARE_MISC, "6.35MM JACK (MONO)", 1, "Output", role=jacks
    )
    inject(
        ComponentCategory.HARDWARE_MISC,
        "DC POWER JACK 2.1MM",
        1,
        "Center Negative",
        role=dc,
    )
    inject(ComponentCategory.HARDWARE_MISC, "Bezel LED Holder", 1, "3mm Metal")
    inject(
        ComponentCategory.HARDWARE_MISC,
        "Rubber Feet (Black)",
        4,
        "Enclosure Feet",
        role=enclosure,
    )
    inject(
        ComponentCategory.HARDWARE_MISC, "AWG 24 Hook-Up Wire", 3, "Approx 1ft/pedal"
    )
    inject(ComponentCategory.HARDWARE_MISC, "9V BATTERY CLIP", 1, "Optional")
    inject(ComponentCategory.HARDWARE_MISC, "Heat Shrink Tubing", 1, "Insulation")

    # 4. Potentiometer Hardware (Knobs/Seals): a trimmer is set on the board
    # and takes neither.
    total_pots = sum(
        d.qty - trimmer_count(k, d)
        for k, d in inventory.items()
        if k.category == ComponentCategory.POTENTIOMETERS
    )
    if total_pots > 0:
        inject(
            ComponentCategory.HARDWARE_MISC,
            "Knob",
            0,
            f"Knobs ({total_pots})",
            qty_override=total_pots,
        )
        inject(
            ComponentCategory.HARDWARE_MISC,
            "Dust Seal Cover",
            0,
            f"Pot Seals ({total_pots})",
            qty_override=total_pots,
        )
    return inventory


def is_pure_hardware(data: PartData) -> bool:
    """Whether a part is only in the hardware kit: no project lists it."""
    return bool(data.hardware) and not data.sources


def is_extra_part(key: ComponentKey) -> bool:
    """Whether a part is the socket Star Ground adds for a chip.

    Known by its key, never by the word 'socket': a document's DC socket or
    tube socket is one of its own parts.
    """
    return key == IC_SOCKET


def determine_origin(key: ComponentKey, data: PartData) -> ComponentOrigin:
    """Classifies component origin as Hardware Kit, Extras, or Circuit Board."""
    if is_pure_hardware(data):
        return ComponentOrigin.HARDWARE_KIT
    if is_extra_part(key):
        return ComponentOrigin.EXTRAS
    return ComponentOrigin.CIRCUIT_BOARD


# Letters after a chip's part number name its package or grade, not another
# chip: 'TL072CP', 'JRC4558D', 'NE5532P' are a TL072, a JRC4558, an NE5532.
_PART_NUMBER = re.compile(r"([A-Z]*\d+)[A-Z]*")


def _base_part_number(val: str) -> str:
    """A chip's part number without its package letters ('jrc4558d' -> 'JRC4558')."""
    upper = val.strip().upper()
    match = _PART_NUMBER.fullmatch(upper)
    return match.group(1) if match else upper


# Designator prefixes for trimmers: 'TRIM1' (PedalPCB), 'TR1' and 'T1'
# (GuitarPCB). A 'VR' is a trimmer in some documents and a panel pot in
# others (Tonepad's 'VR1  100kB'), so it counts as a pot.
_TRIMMER_PREFIXES = frozenset({"TRIM", "TR", "T"})
_REPEAT_SUFFIX = re.compile(r"\s+\(\d+\)$")


def is_trimmer_ref(ref: str) -> bool:
    """Whether a potentiometer's designator marks it as a trimmer.

    Args:
        ref: The designator ('TRIM1', 'TR2', 'T1', 'BIAS TRIM', 'VOLUME').

    Returns:
        True for a trimmer prefix, or a label naming a trim.
    """
    ref = _REPEAT_SUFFIX.sub("", ref.strip())
    designator = parse_designator(ref)
    if designator is not None:
        return designator.prefix in _TRIMMER_PREFIXES
    return "TRIM" in ref.upper()


def trimmer_count(key: ComponentKey, data: PartData) -> int:
    """How many of a potentiometer part are trimmers, not panel pots.

    A trimmer is set once on the board, so it takes no knob or dust seal and
    is bought as a trimmer.

    Args:
        key: The part.
        data: Its quantities and designators.

    Returns:
        All of them for a value that names a trim ('100k trimpots', Tonepad),
        else the number with a trimmer designator (see `is_trimmer_ref`).
    """
    if key.category != ComponentCategory.POTENTIOMETERS:
        return 0
    if "TRIM" in key.value.upper():
        return data.qty
    trimmers = sum(
        is_trimmer_ref(ref) for refs in data.sources.values() for ref in refs
    )
    return min(trimmers, data.qty)


def takes_socket(key: ComponentKey, data: PartData, socket: PartData | None) -> bool:
    """Whether Star Ground added a socket for a chip.

    The classifier injects one per DIP chip, as 'IC1 (Inj)' beside 'IC1', and
    none for a regulator or a module.

    Args:
        key: The chip.
        data: Its quantities and designators.
        socket: The injected sockets (`constants.IC_SOCKET`), if any.

    Returns:
        True if one of its designators has a socket, or if a project counts
        it by value and the classifier gives such a chip one.
    """
    if socket is None:
        return False
    for source, refs in data.sources.items():
        injected = socket.sources.get(source, [])
        if any(f"{ref}{INJECTED_SUFFIX}" in injected for ref in refs):
            return True
        if data.counted.get(source) and socket.counted.get(source):
            result = categorize_part("", key.value, ComponentCategory.ICS)
            if result is not None and result.injected_key is not None:
                return True
    return False


def resolve_part_sourcing(
    key: ComponentKey,
    data: PartData,
    net_qty: int,
    socketed: bool = False,
) -> ResolvedPartSourcing:
    """Resolves the complete purchasing and supplier specification for a component.

    Unifies origin assignment, buffer calculation, hardware notes,
    dielectric spec resolution, and the supplier search.

    Args:
        key: The part.
        data: Its quantities, designators and hardware notes.
        net_qty: Quantity required after deducting stock (deficit).
        socketed: Whether Star Ground added a socket for a chip (see
            `takes_socket`).

    Returns:
        ResolvedPartSourcing with origin, buy_qty, notes, spec_type, search_term, supplier_url.
    """
    category, val = key.category, key.value
    val_qty = key_quantity(key)
    origin = determine_origin(key, data)

    advice = get_buy_details(category, val, net_qty, val_qty=val_qty, socketed=socketed)
    buy_qty = advice.buy_qty
    note = advice.note

    if data.hardware and origin != ComponentOrigin.HARDWARE_KIT:
        note = f"{note} Standard hardware: {', '.join(data.hardware)}.".strip()

    spec_type = get_spec_type(category, val, val_qty=val_qty)
    trimmers = trimmer_count(key, data)
    all_trimmers = 0 < trimmers == data.qty
    search_term = generate_search_term(category, val, spec_type, trimmer=all_trimmers)
    if trimmers and not all_trimmers:
        trimmer_term = generate_search_term(category, val, spec_type, trimmer=True)
        which = "is a trimmer" if trimmers == 1 else "are trimmers"
        note = f"{note} {trimmers} of these {which}: search for '{trimmer_term}'."
        note = note.strip()

    url = generate_tayda_url(search_term)

    return ResolvedPartSourcing(
        origin=origin,
        buy_qty=buy_qty,
        notes=note,
        spec_type=spec_type,
        search_term=search_term,
        supplier_url=url,
    )


def build_shopping_list(
    inventory: Inventory,
    stock: Inventory | None = None,
    show_hardware: bool = True,
    show_extras: bool = True,
) -> list[ShoppingLine]:
    """Assembles the master shopping list across all projects.

    Applies inventory stock deductions, sorts parts according to display hierarchy,
    filters hardware/extras based on user preferences, and resolves purchasing details.

    Args:
        inventory: Aggregated inventory from all projects.
        stock: Optional user stock inventory to calculate net needs.
        show_hardware: Whether to include the hardware kit's own parts.
        show_extras: Whether to include the sockets Star Ground adds for chips.

    Returns:
        One line per part, in display order.
    """
    net_inventory = calculate_net_needs(inventory, stock or Inventory())

    sorted_parts = sort_inventory(inventory)
    shopping_list: list[ShoppingLine] = []
    socket = inventory.get(IC_SOCKET)

    for part_key, item in sorted_parts:
        category, value = part_key.category, part_key.value

        if is_pure_hardware(item) and not show_hardware:
            continue
        if is_extra_part(part_key) and not show_extras:
            continue

        gross_qty = item.qty

        net_item = net_inventory.get(part_key)
        net_qty = net_item.qty if net_item else 0

        in_stock = 0
        if stock:
            s_item = stock.get(part_key)
            in_stock = s_item.qty if s_item else 0

        resolved = resolve_part_sourcing(
            part_key,
            item,
            net_qty,
            socketed=category == ComponentCategory.ICS
            and takes_socket(part_key, item, socket),
        )
        notes = resolved.notes
        if item.included:
            notes = f"{item.included} come with the PCB. {notes}".strip()

        shopping_list.append(
            ShoppingLine(
                origin=resolved.origin,
                category=category,
                part=value,
                bom_qty=gross_qty,
                included=item.included,
                in_stock=in_stock,
                net_need=net_qty,
                buy_qty=resolved.buy_qty,
                notes=notes,
                search_term=resolved.search_term,
                supplier_url=resolved.supplier_url,
            )
        )

    return shopping_list
