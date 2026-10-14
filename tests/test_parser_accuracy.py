"""Ground-truth ratchet for PDF parsing accuracy.

Grades the parser against the hand-verified parts lists in `tests/golden/` and
compares the result to `tests/golden/baseline.json`, the accepted set of known
failures. The test fails on any difference:

- **Regressions** (a new wrong part, a failure that is no longer warned about,
  or a warning on a correctly parsed part) must be fixed.
- **Improvements** must be locked in by re-recording the baseline, so they can
  never silently regress later:

    uv run python -m tests.accuracy --update-baseline

See `tests/accuracy.py` and `tests/golden/README.md` for details.
"""

import logging

import pytest

from tests.accuracy import (
    GoldenDoc,
    compare_to_baseline,
    golden_docs,
    load_baseline,
    score_doc,
)

logging.getLogger("pdfminer").setLevel(logging.WARNING)

DOCS = golden_docs()


@pytest.fixture(scope="module")
def baseline():
    """Loads the accepted failure baseline once per module."""
    return load_baseline()


@pytest.mark.parametrize("doc", DOCS, ids=[d.name for d in DOCS])
def test_accuracy_matches_baseline(doc: GoldenDoc, baseline) -> None:
    """Parser output for each golden document matches the accepted baseline."""
    diffs = compare_to_baseline(score_doc(doc), baseline)
    assert not diffs, (
        f"{doc.name} differs from tests/golden/baseline.json:\n  "
        + "\n  ".join(diffs)
        + "\nFix regressions; lock in improvements with "
        "`uv run python -m tests.accuracy --update-baseline`."
    )


def test_baseline_has_no_stale_documents(baseline) -> None:
    """Every baseline entry corresponds to an existing golden file."""
    assert set(baseline) <= {d.name for d in DOCS}


def test_every_sample_has_golden_truth() -> None:
    """Every sample PDF is covered by a golden file, and vice versa."""
    samples = {
        str(p.relative_to(p.parents[1])) for p in DOCS[0].pdf.parents[1].glob("*/*.pdf")
    }
    golden = {str(d.pdf.relative_to(d.pdf.parents[1])) for d in DOCS}
    assert samples == golden
