"""Routes about the service itself."""

from fastapi import APIRouter

from star_ground.server.models import Health

router = APIRouter(tags=["service"])


@router.get("/api/health")
def health() -> Health:
    """Answers once the server is up; the frontend calls it to wake it."""
    return Health()
