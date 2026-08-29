"""Star Ground BOM Library (Package Entry Point).

Exposes the core logic and data structures for BOM ingestion, classification,
and sourcing.
"""

from .classifier import categorize_part, normalize_value_by_category
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
from .strategies import (
    BOMParserContext,
    BOMParserStrategy,
    CSVParserStrategy,
    ManualInputStrategy,
    ParseResult,
    PDFParserStrategy,
)
from .types import (
    Inventory,
    PartData,
    ProjectSlot,
    StatsDict,
    create_empty_inventory,
)
from .units import ureg
from .utils import (
    deduplicate_refs,
    expand_refs,
    float_to_display_string,
    float_to_search_string,
    get_clean_name,
    parse_value_to_decimal,
    parse_value_to_float,
)

__all__ = [
    "BOM_PRESETS",
    "BOMParserContext",
    "BOMParserStrategy",
    "CSVParserStrategy",
    "Inventory",
    "ManualInputStrategy",
    "PDFParserStrategy",
    "ParseResult",
    "PartData",
    "ProjectSlot",
    "StatsDict",
    "calculate_net_needs",
    "categorize_part",
    "create_empty_inventory",
    "deduplicate_refs",
    "expand_refs",
    "float_to_display_string",
    "float_to_search_string",
    "generate_pedalpcb_url",
    "generate_search_term",
    "generate_tayda_url",
    "get_buy_details",
    "get_clean_name",
    "get_preset_metadata",
    "get_residual_report",
    "get_spec_type",
    "get_standard_hardware",
    "normalize_value_by_category",
    "parse_csv_bom",
    "parse_pedalpcb_pdf",
    "parse_user_inventory",
    "parse_value_to_decimal",
    "parse_value_to_float",
    "parse_with_verification",
    "rename_source_in_inventory",
    "serialize_inventory",
    "sort_inventory",
    "ureg",
]
