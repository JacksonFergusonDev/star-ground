import pytest


def pytest_addoption(parser):
    """Registers custom command-line flags for pytest."""
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Update existing snapshots with current output instead of failing.",
    )


@pytest.fixture
def snapshot_update(request):
    """Returns the value of the --snapshot-update flag.

    Args:
        request: The pytest request object.

    Returns:
        bool: True if the flag was passed, False otherwise.
    """
    return request.config.getoption("--snapshot-update")
