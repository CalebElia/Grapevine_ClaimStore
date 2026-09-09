"""The reviewer UI: the span guard must hold on the click path too.

A button is a cheaper way to assert something than a typed command, which is exactly why the
guard has to be provably the same code. These tests drive review_mentions.store() through a
stub cursor -- no server needed, and the rule is checked on every run.
"""
import pytest

from pipeline import review_mentions as rm
from pipeline.review_server import _guess


class StubCursor:
    """Answers the two queries store() makes, in order."""

    def __init__(self, verbatim: str | None, subject: str | None = "Some Initiative"):
        self.verbatim, self.subject, self.inserted = verbatim, subject, []
        self._next = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        if "FROM claims" in sql:
            self._next = (self.verbatim,) if self.verbatim is not None else None
        elif "FROM subjects" in sql:
            self._next = (self.subject,) if self.subject is not None else None
        elif "INSERT INTO claim_subject_mentions" in sql:
            self.inserted.append(params)
            self._next = None

    def fetchone(self):
        return self._next


V = "Launched a City circular economy website."


def test_a_click_cannot_assert_a_span_the_sentence_lacks():
    with pytest.raises(rm.Refused, match="does not occur"):
        rm.store(StubCursor(V), 1, 177, "nuclear fusion", "caleb")


def test_wrong_case_is_refused_and_the_printed_text_is_offered():
    with pytest.raises(rm.Refused, match="Did you mean 'circular economy'"):
        rm.store(StubCursor(V), 1, 177, "Circular Economy", "caleb")


def test_a_good_ruling_stores_the_document_s_own_text():
    cur = StubCursor(V)
    r = rm.store(cur, 1, 177, "circular economy", "caleb")
    assert (r["span_start"], r["span_end"]) == (16, 32)
    assert r["matched_text"] == "circular economy"
    assert V[r["span_start"]:r["span_end"]] == r["matched_text"]
    assert cur.inserted and cur.inserted[0][5] == "caleb"


def test_dry_run_writes_nothing():
    cur = StubCursor(V)
    r = rm.store(cur, 1, 177, "circular economy", "caleb", dry_run=True)
    assert r["dry_run"] and not r["stored"] and cur.inserted == []


def test_unknown_claim_and_unknown_subject_are_refused():
    with pytest.raises(rm.Refused, match="no claim"):
        rm.store(StubCursor(None), 999, 177, "x", "caleb")
    with pytest.raises(rm.Refused, match="no subject"):
        rm.store(StubCursor(V, subject=None), 1, 999, "circular economy", "caleb")


# ── the phrase the box is prefilled with ──────────────────────────────────────────────────

def test_guess_returns_the_casing_the_page_printed():
    assert _guess('Envisioning a Circular Economy: The Journey', "Move Toward a Circular "
                  "Economy") == "Circular Economy"


def test_guess_is_empty_when_the_name_is_not_there():
    assert _guess("A sentence about buses.", "Move Toward a Circular Economy") == ""


def test_guess_never_returns_a_single_word():
    """One word is never enough -- 'Offsets' would match any sentence using the word."""
    assert _guess("We discussed the economy at length.",
                  "Move Toward a Circular Economy") == ""


def test_guess_output_is_always_sliceable_from_the_verbatim():
    v = "Updated the circular economy GIS map to highlight businesses."
    g = _guess(v, "Move Toward a Circular Economy")
    assert g and v.find(g) >= 0


# ── the empty-phrase crash ────────────────────────────────────────────────────────────────
#
# "".find() returns 0, not -1, so a blank phrase passed the find() guard and reached the
# database as a zero-length span. Only CHECK (span_end > span_start) stopped it, as an opaque
# CheckViolation. The UI hands over a blank whenever no contiguous phrase could be prefilled.

def test_an_empty_phrase_is_refused_before_it_reaches_the_database():
    for blank in ("", "   ", "\t"):
        with pytest.raises(rm.Refused, match="no phrase given"):
            rm.store(StubCursor(V), 561, 244, blank, "caleb")


def test_the_empty_phrase_refusal_says_what_to_do_instead():
    with pytest.raises(rm.Refused, match="rule it No"):
        rm.store(StubCursor(V), 561, 244, "", "caleb")


# ── helping the reviewer when no contiguous phrase exists ─────────────────────────────────

CODES = ("Continued pushing for updates to the Michigan building and energy codes with "
         "multiple staff testifying before the State committee.")


def test_word_hits_locates_scattered_name_words():
    from pipeline.review_server import word_hits
    hits = {h["word"]: h["at"] for h in word_hits(CODES, "Update Building Codes")}
    assert hits == {"update": 22, "building": 46, "codes": 66}


def test_word_hits_skips_short_words():
    from pipeline.review_server import word_hits
    assert all(len(h["word"]) > 3
               for h in word_hits("we move toward a circular economy",
                                  "Move Toward a Circular Economy"))


def test_the_suggested_run_is_always_a_real_substring():
    from pipeline.review_server import word_hits, enclosing_run
    run = enclosing_run(CODES, word_hits(CODES, "Update Building Codes"))
    assert run == "updates to the Michigan building and energy codes"
    assert CODES.find(run) >= 0


def test_no_run_is_suggested_when_a_name_word_is_absent():
    """A run that skips a missing word would prove something the sentence never said."""
    from pipeline.review_server import word_hits, enclosing_run
    v = "Continued pushing for building improvements."
    assert enclosing_run(v, word_hits(v, "Update Building Codes")) == ""


def test_no_run_is_suggested_when_it_would_be_most_of_the_sentence():
    from pipeline.review_server import word_hits, enclosing_run
    v = ("Update the plan, and after a great many intervening words that go on for quite a "
         "while indeed and then some more, revisit the building codes.")
    assert enclosing_run(v, word_hits(v, "Update Building Codes")) == ""


def test_a_suggested_run_survives_the_span_guard():
    """Whatever the UI offers must be storable, or the button is a trap."""
    from pipeline.review_server import word_hits, enclosing_run
    run = enclosing_run(CODES, word_hits(CODES, "Update Building Codes"))
    r = rm.store(StubCursor(CODES), 561, 244, run, "caleb")
    assert CODES[r["span_start"]:r["span_end"]] == run


def test_the_socket_binds_before_the_browser_is_opened():
    """A browser opened before the bind races a port with nothing listening.

    It failed as "the server takes forever to start" when the server was already up and the
    tab was simply stale. Source order is the only place this is checkable without launching
    a real browser, and it is the exact thing that regressed.
    """
    import inspect

    from pipeline import review_server
    src = inspect.getsource(review_server.main)
    assert src.index("ThreadingHTTPServer((") < src.index("webbrowser.open("), \
        "webbrowser.open() must come after the socket is bound"


def test_startup_does_not_do_a_database_pass_before_binding():
    """Any work in front of the bind widens the window the browser can lose."""
    import inspect

    from pipeline import review_server
    src = inspect.getsource(review_server.main)
    head = src[:src.index("ThreadingHTTPServer((")]
    assert "items(" not in head, "counting the queue before binding delays the socket"


# ── word boundaries ───────────────────────────────────────────────────────────────────────
#
# A review session stored `oint campaign entitled "The Future is Electric"` -- literally
# present in the claim, and a slicing artifact of "joint". Occurring in the text is necessary
# but not sufficient: a mention has to start and end where a word does.

JOINT = 'Partnered with IBEW/NECA to launch a joint campaign entitled "The Future is Electric".'


def test_a_span_starting_mid_word_is_refused():
    with pytest.raises(rm.Refused, match="mid-word"):
        rm.store(StubCursor(JOINT), 1209, 141, "oint campaign", "caleb")


def test_a_span_ending_mid_word_is_refused():
    with pytest.raises(rm.Refused, match="mid-word"):
        rm.store(StubCursor(JOINT), 1209, 141, "joint campa", "caleb")


def test_the_refusal_offers_the_word_aligned_span():
    with pytest.raises(rm.Refused, match="'joint campaign'"):
        rm.store(StubCursor(JOINT), 1209, 141, "oint campaign", "caleb")


def test_a_well_formed_span_still_stores():
    r = rm.store(StubCursor(JOINT), 1209, 141, "joint campaign", "caleb")
    assert JOINT[r["span_start"]:r["span_end"]] == "joint campaign"


def test_punctuation_at_the_edges_is_not_mid_word():
    """A phrase may legitimately begin or end on a quote, bracket or hyphen."""
    r = rm.store(StubCursor(JOINT), 1209, 141, '"The Future is Electric"', "caleb")
    assert r["matched_text"] == '"The Future is Electric"'


def test_a_span_at_the_very_start_of_the_claim_is_allowed():
    r = rm.store(StubCursor(JOINT), 1209, 141, "Partnered with", "caleb")
    assert r["span_start"] == 0
