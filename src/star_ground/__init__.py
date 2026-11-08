"""Star Ground: turns pedal build documents into what to buy and how to build."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version


def version() -> str:
    """Star Ground's installed version, or 'dev' when run from a bare checkout."""
    try:
        return _metadata_version("star-ground")
    except PackageNotFoundError:
        return "dev"
