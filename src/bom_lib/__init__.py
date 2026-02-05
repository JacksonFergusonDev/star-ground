"""
Star Ground BOM Library (Package Entry Point).

Exposes the core logic and data structures for BOM ingestion, classification,
and sourcing.
"""

from .manager import (
    calculate_net_needs,
    merge_inventory,
    rename_source_in_inventory,
    serialize_inventory,
    sort_inventory,
)
from .parser import (
    parse_csv_bom,
    parse_pedalpcb_pdf,
    parse_user_inventory,
    parse_with_verification,
)
from .presets import BOM_PRESETS, get_preset_metadata
from .sourcing import (
    generate_pedalpcb_url,
    generate_search_term,
    generate_tayda_url,
    get_buy_details,
    get_residual_report,
    get_spec_type,
    get_standard_hardware,
)
from .types import InventoryType, StatsDict, create_empty_inventory
from .utils import (
    deduplicate_refs,
    expand_refs,
    float_to_display_string,
    float_to_search_string,
    get_clean_name,
    parse_value_to_float,
)

__all__ = [
    # types
    "InventoryType",
    "StatsDict",
    "create_empty_inventory",
    # parser
    "parse_csv_bom",
    "parse_pedalpcb_pdf",
    "parse_user_inventory",
    "parse_with_verification",
    # manager
    "calculate_net_needs",
    "rename_source_in_inventory",
    "sort_inventory",
    "serialize_inventory",
    "generate_pedalpcb_url",
    "generate_search_term",
    "generate_tayda_url",
    "get_buy_details",
    "get_residual_report",
    "get_spec_type",
    "get_standard_hardware",
    "merge_inventory",
    # utils
    "deduplicate_refs",
    "expand_refs",
    "float_to_display_string",
    "float_to_search_string",
    "parse_value_to_float",
    "get_clean_name",
    # presets
    "get_preset_metadata",
    "BOM_PRESETS",
]
