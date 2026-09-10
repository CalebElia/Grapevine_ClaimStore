"""award_status: what the documents say about an award's fate after it was made.

All 75 fiscal references said 'unknown' because the status is almost never in the sentence
that names the amount. "won $10 million from the Department of Energy" is one claim;
"(Funding is currently on hold)" is a different claim two sentences later with no dollar
figure, so no fiscal_references row was ever made for it.
"""
import os

import pytest

from pipeline.award_status import apply_status, verb_status

DSN = os.environ.get("GRAPEVINE_DSN",
                     "host=/tmp port=5433 user=grapevine dbname=grapevine")


def _db():
    try:
        import psycopg
        psycopg.connect(DSN, connect_timeout=3).close()
        return True
    except Exception:
        return False


# ── the verb inside the claim's own sentence ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "OSI successfully won $10,000,000 to support deployment",
    "The City successfully secured $5,000,000 to help launch the SEU",
    "Secured $5,000,000 award to help advance solar",
    "received grant funding to support the creation of a plan",
    "was granted $50,000",
])
def test_an_award_verb_gives_awarded(text):
    assert verb_status(text)[0] == "awarded"


@pytest.mark.parametrize("text", [
    "we submitted a grant application to launch a city-wide program",
    "The City applied for federal funds",
])
def test_an_application_verb_gives_applied(text):
    assert verb_status(text)[0] == "applied"


def test_a_silent_claim_gives_nothing():
    """54 rows state an amount and nothing about its fate. 'unknown' is the true answer."""
    assert verb_status("Urban Sustainability Directors Network Mini-Grant ($20,000).") is None


def test_the_matched_words_come_back_as_evidence():
    """What is highlighted in the UI has to be what the rule actually matched."""
    status, evidence = verb_status("OSI successfully won $10,000,000")
    assert evidence.lower() == "won"


def test_the_verb_must_be_a_whole_word():
    """'grantee' is not 'granted'; 'wonder' is not 'won'."""
    assert verb_status("the grantee list was published") is None
    assert verb_status("a wonderful year for the programme") is None


# ── writing a status ──────────────────────────────────────────────────────────────────────

class StubCursor:
    def __init__(self, terms):
        self.terms, self.updates, self._next = terms, [], None
        self.rowcount = 1

    def execute(self, sql, params=None):
        if "vocabulary_terms" in sql:
            self._rows = [(t,) for t in self.terms]
        elif sql.strip().upper().startswith("UPDATE"):
            self.updates.append(params)

    def fetchall(self):
        return self._rows


TERMS = ["applied", "awarded", "disbursed", "disputed", "lapsed", "obligated", "on_hold",
         "rescinded", "terminated", "unknown", "withdrawn"]


def test_an_unapproved_status_is_refused():
    with pytest.raises(ValueError, match="not an approved award_status"):
        apply_status(StubCursor(TERMS), 1, "vibes", "because", None, "tester")


def test_a_status_without_its_evidence_is_refused():
    """status_change_reason IS the evidence. A status with no quoted sentence behind it is
    the bare assertion this table exists to avoid."""
    with pytest.raises(ValueError, match="needs status_change_reason"):
        apply_status(StubCursor(TERMS), 1, "terminated", "   ", None, "tester")


def test_unknown_needs_no_evidence():
    """Saying you do not know requires nothing; saying you do requires a sentence."""
    r = apply_status(StubCursor(TERMS), 1, "unknown", None, None, "tester")
    assert r["status"] == "unknown"


def test_a_good_ruling_writes_the_reason():
    cur = StubCursor(TERMS)
    apply_status(cur, 188, "on_hold", "(Funding is currently on hold).", "2025-05-31", "t")
    assert cur.updates and "(Funding is currently on hold)." in cur.updates[0]


# ── the ordering rule, against the real corpus ────────────────────────────────────────────

@pytest.mark.skipif(not _db(), reason="no database")
def test_a_reversal_outranks_the_award_verb_that_precedes_it():
    """Fiscal 188 says BOTH "won $10 million" and "(Funding is currently on hold)". A verb
    rule applied without checking for a reversal marks it `awarded` -- a playbook recommending
    a frozen grant, which is the failure migration 012 was written to prevent."""
    import psycopg
    from pipeline.award_status import classify
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        g = classify(cur)
    queued = {r["fiscal_id"] for r in g["reversal"]}
    auto = {r["fiscal_id"] for r in g["verb"]}
    assert 188 in queued, "the $10M DOE grant is on hold and must not be auto-marked awarded"
    assert 188 not in auto
    assert not (queued & auto), "a row cannot be both queued and automatic"


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_fiscal_reference_lands_in_exactly_one_group():
    import psycopg
    from pipeline.award_status import classify
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        g = classify(cur)
        total = cur.execute("SELECT count(*) FROM fiscal_references").fetchone()[0]
    assert len(g["verb"]) + len(g["reversal"]) + len(g["silent"]) == total
