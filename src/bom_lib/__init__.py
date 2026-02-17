"""
Star Ground BOM Library (Package Entry Point).

Exposes the core logic and data structures for BOM ingestion, classification,
and sourcing.
"""

from .classifier import categorize_part, normalize_value_by_category
from .loader import process_input_data
from .manager import (
    calculate_net_needs,
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
from .types import (
    Inventory,
    PartData,
    ProjectSlot,
    StatsDict,
    create_empty_inventory,
)
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
    "StatsDict",
    "create_empty_inventory",
    "PartData",
    "Inventory",
    "ProjectSlot",
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
    # loader
    "process_input_data",
    # classifier
    "categorize_part",
    "normalize_value_by_category",
]
