"""The HTTP API the web frontend calls (see `star_ground.server.app`)."""

from star_ground.server.app import Settings, create_app, feedback_from_env
from star_ground.server.logs import configure_logging

configure_logging()
_sheet = feedback_from_env()
app = create_app(
    Settings.from_env(),
    feedback=_sheet,
    part_reports=_sheet.part_report if _sheet else None,
)
"""The application `uvicorn star_ground.server:app` serves, configured from the environment."""

__all__ = ["Settings", "app", "create_app"]
