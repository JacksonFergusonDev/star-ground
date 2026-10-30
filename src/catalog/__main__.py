"""Runs the catalog job: `python -m src.catalog --help`."""

import argparse
import sys
import time
from pathlib import Path

from src.catalog.build import Budget, build
from src.catalog.client import MIN_DELAY_S, PoliteClient, user_agent
from src.catalog.report import Outcome, Reporter
from src.catalog.sources import SOURCES, Publisher


def main(argv: list[str] | None = None) -> int:
    """Brings the catalog up to date, and reports every outcome.

    Returns:
        0, or 1 when a document or page failed or a site turned the job away,
        so a scheduled run that needs a look shows as failed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.catalog",
        description="Find publishers' build documents, read them, and keep what "
        "was derived (never the documents).",
    )
    parser.add_argument(
        "--publisher",
        action="append",
        choices=[p.value for p in Publisher],
        help="Only this publisher (repeatable); all by default.",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("catalog"), help="The catalog's directory."
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=300,
        help="Stop starting new documents after this long (default 300, under GitHub's 6-hour job limit).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=MIN_DELAY_S,
        help=f"Least seconds between requests to one site (default {MIN_DELAY_S:.0f}).",
    )
    parser.add_argument(
        "--pages",
        type=int,
        help="Sample: read at most this many listing pages per publisher.",
    )
    parser.add_argument(
        "--documents",
        type=int,
        help="Sample: read at most this many documents per publisher.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log every request.")
    args = parser.parse_args(argv)

    reporter = Reporter.for_environment()
    reporter.install_logging(args.verbose)
    if args.delay < MIN_DELAY_S:
        reporter.warning(
            f"--delay {args.delay} is below the minimum; using {MIN_DELAY_S:.0f} s"
        )
    client = PoliteClient(delay=max(args.delay, MIN_DELAY_S))
    budget = Budget(time.monotonic() + args.minutes * 60)
    publishers = (
        [Publisher(p) for p in args.publisher] if args.publisher else list(Publisher)
    )
    reporter.info(
        f"Catalog job as '{user_agent()}': {', '.join(publishers)}; at least "
        f"{max(args.delay, MIN_DELAY_S):.0f} s between requests to a site; "
        f"{args.minutes:.0f} minutes for documents."
    )
    started = time.monotonic()
    for publisher in publishers:
        try:
            build(
                client,
                SOURCES[publisher],
                args.out,
                reporter,
                budget,
                args.pages,
                args.documents,
            )
        except Exception as error:  # One publisher failing doesn't stop the others.
            reporter.error(
                f"{type(error).__name__}: {error}", title=f"{publisher}: run stopped"
            )
            reporter.tally.page_failures.setdefault(publisher, {})["(run)"] = str(error)
    reporter.info(
        f"{client.requests} requests in {(time.monotonic() - started) / 60:.1f} minutes"
    )
    reporter.summary()
    events = reporter.tally.events
    trouble = any(e.outcome in (Outcome.FAILED, Outcome.BLOCKED) for e in events)
    return 1 if trouble or any(reporter.tally.page_failures.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
