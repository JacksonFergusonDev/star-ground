"""Writes the API's OpenAPI schema to `openapi.json`.

The frontend generates its TypeScript types from this file, so it's committed
and a test fails when it no longer matches the API. Run `just openapi` after
changing a route or model in `src/server/`.
"""

import json
from pathlib import Path

from src.server import Settings, create_app

OPENAPI_FILE = Path(__file__).resolve().parent.parent / "openapi.json"


def schema_text() -> str:
    """The API's current OpenAPI schema, formatted as committed.

    Returns:
        The schema as indented JSON with a trailing newline.
    """
    schema = create_app(Settings()).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    """Writes the schema to `openapi.json`."""
    OPENAPI_FILE.write_text(schema_text(), encoding="utf-8")
    print(f"Wrote {OPENAPI_FILE.name}")


if __name__ == "__main__":
    main()
