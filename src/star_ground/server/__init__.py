"""The HTTP API the web frontend calls (see `star_ground.server.app`)."""

from star_ground.server.app import Settings, create_app
from star_ground.server.logs import configure_logging

configure_logging()
app = create_app(Settings.from_env())
"""The application `uvicorn star_ground.server:app` serves, configured from the environment."""

__all__ = ["Settings", "app", "create_app"]
