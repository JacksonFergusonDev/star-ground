"""Star Ground BOM Library (Package Entry Point).

Exposes the core logic and data structures for BOM ingestion, classification,
and sourcing.
"""

from .classifier import (
    categorize_part,
    normalize_value_by_category,
    normalize_value_to_quantity,
)
from .enums import ComponentCategory, ComponentSpec, FeedbackRating, InputMethod
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
from .presets import (
    BOM_PRESETS,
    PresetCatalog,
    PresetLookupEntry,
    get_preset_metadata,
)
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
    AlternativeSpec,
    ChecklistPart,
    Inventory,
    PartData,
    ProjectSlot,
    ShoppingListRow,
    StatsDict,
    create_empty_inventory,
    create_empty_stats,
)
from .units import ureg
from .utils import (
    deduplicate_refs,
    expand_refs,
    float_to_display_string,
    float_to_search_string,
    get_clean_name,
    parse_value_to_decimal,
)

__all__ = [
    "BOM_PRESETS",
    "AlternativeSpec",
    "BOMParserContext",
    "BOMParserStrategy",
    "CSVParserStrategy",
    "ChecklistPart",
    "ComponentCategory",
    "ComponentSpec",
    "FeedbackRating",
    "InputMethod",
    "Inventory",
    "ManualInputStrategy",
    "PDFParserStrategy",
    "ParseResult",
    "PartData",
    "PresetCatalog",
    "PresetLookupEntry",
    "ProjectSlot",
    "ShoppingListRow",
    "StatsDict",
    "calculate_net_needs",
    "categorize_part",
    "create_empty_inventory",
    "create_empty_stats",
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
    "normalize_value_to_quantity",
    "parse_csv_bom",
    "parse_pedalpcb_pdf",
    "parse_user_inventory",
    "parse_value_to_decimal",
    "parse_with_verification",
    "rename_source_in_inventory",
    "serialize_inventory",
    "sort_inventory",
    "ureg",
]
