"""Context-Free Grammar (CFG) parsers for Star Ground.

Provides combinator-based parsing primitives for BOM components and SI values.
"""

from star_ground.bom_lib.grammar.value_parser import parse_value_to_decimal

__all__ = ["parse_value_to_decimal"]
