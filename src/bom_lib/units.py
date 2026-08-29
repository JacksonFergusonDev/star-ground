"""Physical units registry for the Star Ground Engine.

Provides a shared `pint.UnitRegistry` instance for strict dimensional
analysis and component value manipulation.
"""

from typing import Any

import pint

ureg: pint.UnitRegistry[Any] = pint.UnitRegistry()

__all__ = ["ureg"]
