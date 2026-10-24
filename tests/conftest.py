import pytest

from tools.fetch_samples import load_manifest, missing


def pytest_sessionstart(session):
    """Stops the run when the test corpus hasn't been downloaded.

    The corpus's PDFs belong to their publishers and aren't in the
    repository; `just fetch-samples` downloads them.
    """
    if absent := missing(load_manifest()):
        raise pytest.UsageError(
            f"{len(absent)} test sample(s) missing, e.g. tests/samples/"
            f"{absent[0].path}. Run `just fetch-samples` to download them."
        )


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
