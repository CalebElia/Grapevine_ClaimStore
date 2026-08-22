"""A human verdict is separate from the machine's, attributed, and lapses."""
from __future__ import annotations

import pytest

from pipeline.approve_sections import _VERDICTS, approve


def test_approval_requires_a_person_and_a_note():
    """An anonymous gate pass is not a review. The note matters because a reviewer who read
    prose for sense has not checked that OCR read '$45,800' and not '$45,300' -- different
    warrants, and the note is where the difference is recorded."""
    with pytest.raises(SystemExit):
        approve(9, "approved", by="", note="checked it", dry_run=True)
    with pytest.raises(SystemExit):
        approve(9, "approved", by="caleb", note="", dry_run=True)


def test_not_reviewed_needs_no_attribution():
    """Clearing a verdict is not itself a judgement."""
    approve(9, "not_reviewed", by="", note="", dry_run=True)


def test_unknown_verdicts_are_refused():
    with pytest.raises(SystemExit):
        approve(9, "looks_fine", by="caleb", note="n", dry_run=True)


def test_the_vocabulary_matches_the_migration():
    assert set(_VERDICTS) == {"approved", "approved_with_caveats", "rejected",
                              "not_reviewed"}
