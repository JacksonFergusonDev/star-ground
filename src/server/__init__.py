"""The HTTP API the web frontend calls (see `src.server.app`)."""

from src.server.app import Settings, create_app, feedback_from_env

app = create_app(Settings.from_env(), feedback_from_env())
"""The application `uvicorn src.server:app` serves, configured from the environment."""

__all__ = ["Settings", "app", "create_app"]
