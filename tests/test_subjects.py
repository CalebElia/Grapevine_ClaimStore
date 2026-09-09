"""Assigning subjects from the reports' own structure.

Every one of the five reports organises itself into the same seven A2ZERO strategies, and
titles them differently each year: "Strategy 1: Power our electrical grid with 100% renewable
energy" in Year 1, "STRATEGY ONE: POWER OUR ELECTRICAL GRID..." in Year 3, "STRATEGY 1: 100%
RENEWABLES" in Year 5. Same subject, three titles.

That regularity is EVIDENCE, not inference: the document says which strategy a section is
about by putting it in that section. So the subject comes from the structure and is decided
by string matching, not by a model.
"""
from __future__ import annotations

from pipeline.subjects import load_section_rules, strategy_number

RULES = load_section_rules("ann_arbor", "annual_report")
SEVEN = {1, 2, 3, 4, 5, 6, 7}


def test_a_numbered_strategy_heading_yields_its_number():
    assert strategy_number("Strategy 1: Power our electrical grid with 100% renewable energy") == 1
    assert strategy_number("STRATEGY 5: CIRCULAR ECONOMY") == 5


def test_a_spelled_out_strategy_heading_yields_its_number():
    """Year 3 spells them out."""
    assert strategy_number("STRATEGY ONE: POWER OUR ELECTRICAL GRID WITH 100% RENEWABLE ENERGY") == 1
    assert strategy_number("STRATEGY SEVEN: OTHER STRATEGIES (NOTABLE ACCOMPLISHMENTS OR EFFORTS)") == 7


def test_a_dash_separator_works_as_well_as_a_colon():
    assert strategy_number("Strategy 7 - Other strategies/accomplishments") == 7


def test_a_year_heading_is_not_a_strategy():
    """THE TRAP. 'YEAR 5 PRIORITIES' contains a 5 and is not Strategy 5. The pattern has to
    anchor on the word STRATEGY, not on a digit anywhere in the heading."""
    assert strategy_number("YEAR 5 PRIORITIES") is None
    assert strategy_number("YEAR FOUR PRIORITIES") is None
    assert strategy_number("A2ZERO Year 3 Priorities") is None
    assert strategy_number("2021 - 2022 Annual Report") is None


def test_cross_cutting_headings_are_not_strategies():
    for h in ("INTRODUCTION", "GREENHOUSE GAS EMISSIONS SUMMARY", "CONTENTS",
              "CLOSING", "Overview", "Next Steps"):
        assert strategy_number(h) is None, h


def test_a_strategy_number_outside_the_seeded_set_is_refused():
    """A2ZERO has seven, and that fact now comes from the seeded subjects rather than from a
    constant in the code -- so another jurisdiction's plan needs no edit here."""
    assert strategy_number("STRATEGY 8: SOMETHING NEW", valid=SEVEN) is None
    assert strategy_number("STRATEGY 0: NOTHING", valid=SEVEN) is None


def test_missing_and_empty_headings_are_safe():
    assert strategy_number(None) is None
    assert strategy_number("") is None


# --- sections that are not strategies ----------------------------------------------------

from pipeline.subjects import cross_cutting_subject


def test_narrative_sections_belong_to_the_plan_itself():
    """Introductions, overviews, priorities and closings speak about A2ZERO as a whole
    rather than about one strategy, so they get the parent subject rather than none. A claim
    with no subject is invisible to every aggregate."""
    for h in ("INTRODUCTION", "Overview", "CLOSING", "Next Steps",
              "YEAR 5 PRIORITIES", "YEAR FOUR PRIORITIES", "A2ZERO Year 3 Priorities"):
        assert cross_cutting_subject(h, RULES) == "a2zero", h


def test_the_emissions_summary_is_its_own_subject():
    """Every report from Year 3 on opens with a community-wide GHG inventory. It is the
    measurement the whole plan is judged against, not one strategy's business."""
    assert cross_cutting_subject("GREENHOUSE GAS EMISSIONS SUMMARY", RULES) == "ghg_emissions"


def test_navigational_sections_get_no_subject():
    """A table of contents is not about anything. Neither is a repeated cover title -- and
    inventing a subject for them would put structural furniture into topic aggregates."""
    for h in ("CONTENTS", "2021 - 2022 Annual Report", "YEAR THREE ANNUAL REPORT", None, ""):
        assert cross_cutting_subject(h, RULES) is None, h


def test_a_strategy_heading_is_not_cross_cutting():
    """The two functions must not both claim the same section."""
    assert cross_cutting_subject("STRATEGY 5: CIRCULAR ECONOMY", RULES) is None


def test_front_matter_belongs_to_the_plan():
    """Everything before the first heading is the report's own framing of A2ZERO. Year 2's
    front matter carries five claims and all five are about the plan itself -- "A2ZERO is
    Ann Arbor's plan for achieving a just transition to community-wide carbon neutrality by
    2030". Other years put the same material under an INTRODUCTION heading.

    Front matter that holds only a title and a sign-off produces no claims, so the rule
    costs nothing where it does not apply."""
    from pipeline.subjects import section_subject_key
    assert section_subject_key(None, True, RULES, SEVEN) == "a2zero"
    assert section_subject_key("CONTENTS", False, RULES, SEVEN) is None


def test_section_subject_key_combines_both_rules():
    from pipeline.subjects import section_subject_key
    assert section_subject_key("STRATEGY 3: ENERGY EFFICIENCY", False, RULES, SEVEN) == "strategy-3"
    assert section_subject_key("INTRODUCTION", False, RULES, SEVEN) == "a2zero"
    assert section_subject_key("GREENHOUSE GAS EMISSIONS SUMMARY", False, RULES, SEVEN) == "ghg_emissions"
    assert section_subject_key("CONTENTS", False, RULES, SEVEN) is None

    # A doc type with no structural route maps NOTHING, which is the normal case.
    assert section_subject_key("STRATEGY 3: ENERGY EFFICIENCY", False, None, SEVEN) is None


# --- the assigned corpus -----------------------------------------------------------------

def _db():
    try:
        import psycopg
        psycopg.connect("host=/tmp port=5433 user=grapevine dbname=grapevine").close()
        return True
    except Exception:
        return False


import pytest

DSN = "host=/tmp port=5433 user=grapevine dbname=grapevine"


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_annual_report_claim_has_a_subject():
    """SCOPED, because the assertion only ever held for the corpus it was written against.

    Unscoped this read "every claim in the store", which made it a claim about documents not
    yet ingested. The CAP's Action sections legitimately have no subject: an Action names an
    initiative, and which initiative is a match against `subjects`, not a heading regex. That
    gap is asserted separately below so it stays visible rather than disappearing here.
    """
    import psycopg
    with psycopg.connect(DSN) as c:
        missing, total = c.execute(
            "SELECT count(*) FILTER (WHERE cl.subject_id IS NULL), count(*) FROM claims cl "
            "JOIN document_sections s ON s.id = cl.document_section_id "
            "JOIN documents d ON d.id = s.document_id "
            "WHERE d.doc_type = 'annual_report'").fetchone()
    assert missing == 0, f"{missing} of {total} annual-report claims have no subject"


@pytest.mark.skipif(not _db(), reason="no database")
def test_the_plan_claims_without_a_subject_are_a_known_gap():
    """A GAP THAT IS MEASURED IS NOT A SILENT ONE.

    The CAP's Strategy sections resolve through the registry; its 44 Action sections do not,
    by design. This records the size of that gap so the resolver's arrival is visible as a
    number going down, and so a REGRESSION -- claims losing subjects they had -- fails here
    rather than looking like more of the same.
    """
    import psycopg
    with psycopg.connect(DSN) as c:
        rows = c.execute(
            "SELECT count(*) FILTER (WHERE cl.subject_id IS NULL), count(*) FROM claims cl "
            "JOIN document_sections s ON s.id = cl.document_section_id "
            "JOIN documents d ON d.id = s.document_id WHERE d.doc_type = 'plan'").fetchone()
    missing, total = rows
    if total == 0:
        pytest.skip("no plan ingested")
    assert missing < total, "every plan claim lacks a subject — the registry is not applying"
    assert missing / total < 0.95, f"{missing} of {total} — worse than when measured"


@pytest.mark.skipif(not _db(), reason="no database")
def test_navigational_sections_of_a_report_carry_no_claims():
    """Navigational sections get no subject on purpose. If one ever holds a claim, the claim
    is invisible to every topic aggregate -- so the two facts must stay consistent.

    SCOPED TO ANNUAL REPORTS. Unscoped, this said "a section with no subject is navigational",
    which was true of a corpus where every non-navigational section resolved through a heading
    rule. The CAP breaks the premise rather than the invariant: its Action sections are full of
    claims and have no subject because an Action names an INITIATIVE, and which initiative is a
    match against `subjects` rather than a regex. That gap is measured in
    test_the_plan_claims_without_a_subject_are_a_known_gap, not hidden here.
    """
    import psycopg
    with psycopg.connect(DSN) as c:
        rows = c.execute(
            "SELECT s.id, count(cl.*) FROM document_sections s "
            "JOIN documents d ON d.id = s.document_id "
            "LEFT JOIN claims cl ON cl.document_section_id = s.id "
            "WHERE s.subject_id IS NULL AND d.doc_type = 'annual_report' "
            "GROUP BY s.id HAVING count(cl.*) > 0").fetchall()
    assert rows == [], f"sections with claims but no subject: {rows}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_all_seven_strategies_are_present_in_all_five_reports():
    """The structural regularity this whole mapping rests on. If a report ever stops having
    seven strategy sections, the assignment is silently incomplete rather than wrong."""
    import psycopg
    with psycopg.connect(DSN) as c:
        rows = c.execute(
            "SELECT d.id, count(DISTINCT s.subject_id) FROM documents d "
            "JOIN document_sections s ON s.document_id = d.id "
            "JOIN subjects sub ON sub.id = s.subject_id "
            "WHERE sub.name LIKE 'Strategy %' AND d.doc_type = 'annual_report' "
            "GROUP BY d.id ORDER BY d.id").fetchall()
    assert len(rows) == 5, ("expected the five annual reports; a sixth document here means "
                            "the query is no longer scoped to the corpus it describes")
    assert all(n == 7 for _, n in rows), rows


@pytest.mark.skipif(not _db(), reason="no database")
def test_no_subject_is_unreachable_from_the_plan():
    """REWRITTEN, because the initiative layer changed what "reachable" means.

    This asserted every subject had a parent_subject_id, which was true when the only
    subjects were the plan, seven strategies and Bryant. It is now false BY DESIGN: an
    initiative reaches its strategies through subject_framework_categories, because it may
    advance more than one and a tree holds one parent; and a place is not in the taxonomy at
    all, because Bryant is where work happens rather than a kind of work.

    The invariant the original test was defending still matters -- Bryant sat unreachable
    from any roll-up until it was reparented -- so it is stated over every route: a subject
    must hang under a parent, OR carry a framework category, OR be somewhere work happens.

    MERGED DUPLICATES ARE EXEMPT, and their unreachability is the point. A tombstone has had
    every link repointed to its survivor; it exists only so pipeline/initiatives.py can
    resolve the wiki page that created it instead of recreating the duplicate. Requiring it
    to stay reachable would mean leaving it attached to the taxonomy, which is exactly the
    double-counting a merge removes. Hence `live_subjects`, not `subjects`.
    """
    import psycopg
    with psycopg.connect(DSN) as c:
        orphans = c.execute("""
            SELECT s.name FROM live_subjects s
            WHERE s.parent_subject_id IS NULL
              AND s.subject_kind <> 'plan'
              AND NOT EXISTS (SELECT 1 FROM subject_framework_categories f
                              WHERE f.subject_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM subject_places p
                              WHERE p.place_subject_id = s.id)
        """).fetchall()
    assert orphans == [], f"subjects reachable by no route: {orphans[:8]}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_every_tombstone_points_at_a_live_survivor():
    """The exemption above is only safe if a tombstone always leads somewhere. A merged
    subject whose survivor was itself merged would strand every row that followed it."""
    import psycopg
    with psycopg.connect(DSN) as c:
        bad = c.execute("""
            SELECT d.id, d.name FROM subjects d
             WHERE d.merged_into_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM live_subjects s WHERE s.id = d.merged_into_id)
        """).fetchall()
    assert bad == [], f"tombstones pointing at a non-live subject: {bad}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_nothing_still_points_at_a_tombstone():
    """A merge that missed a foreign key leaves rows attached to a dead subject, and nothing
    errors. merge_subjects generates its sweep from pg_constraint precisely so this holds."""
    import psycopg
    with psycopg.connect(DSN) as c:
        cols = c.execute("""
            SELECT c.conrelid::regclass::text, a.attname
              FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
             WHERE c.confrelid='subjects'::regclass AND c.contype='f'
        """).fetchall()
        stranded = []
        for table, col in cols:
            if table == "subjects" and col == "merged_into_id":
                continue
            n = c.execute(f"""SELECT count(*) FROM {table} t
                               JOIN subjects s ON s.id = t.{col}
                              WHERE s.merged_into_id IS NOT NULL""").fetchone()[0]
            if n:
                stranded.append((f"{table}.{col}", n))
    assert stranded == [], f"rows still pointing at merged subjects: {stranded}"


# ── stored mentions: invariants that only hold against real data ──────────────────────────

@pytest.mark.skipif(not _db(), reason="no database")
def test_every_stored_span_slices_back_to_its_matched_text():
    """The whole discipline in one query. test_detect_mentions asserts this of the matcher's
    output; this asserts it of what actually landed, including every human ruling."""
    import psycopg
    with psycopg.connect(DSN) as c:
        bad = c.execute("""
            SELECT m.id, m.matched_text
              FROM claim_subject_mentions m JOIN claims cl ON cl.id = m.claim_id
             WHERE substring(cl.verbatim from m.span_start + 1
                             for m.span_end - m.span_start) <> m.matched_text
        """).fetchall()
    assert bad == [], f"spans that do not slice back: {bad[:5]}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_no_two_mentions_of_one_subject_overlap_in_one_claim():
    """Two annotations of the SAME occurrence double-count it.

    The unique key is (claim_id, subject_id, span_start), which permits a sentence to name an
    initiative twice at two positions -- deliberately, because a second occurrence is also
    evidence. It cannot see two spans that OVERLAP, and merging two subjects is exactly what
    produces them: claim 1209 held `campaign entitled "The Future is Electric"` under 141 and
    a longer span of the same words under 106, which became one subject.
    """
    import psycopg
    with psycopg.connect(DSN) as c:
        overlaps = c.execute("""
            SELECT a.claim_id, a.subject_id, a.span_start, b.span_start
              FROM claim_subject_mentions a
              JOIN claim_subject_mentions b
                ON b.claim_id = a.claim_id AND b.subject_id = a.subject_id
               AND b.span_start > a.span_start AND b.span_start < a.span_end
        """).fetchall()
    assert overlaps == [], f"overlapping spans on one (claim, subject): {overlaps[:5]}"


@pytest.mark.skipif(not _db(), reason="no database")
def test_no_stored_span_starts_or_ends_mid_word():
    """Occurring in the text is necessary, not sufficient. A review session stored
    `oint campaign entitled ...` -- provable, and a slicing artifact of "joint".
    review_mentions.store now refuses these; this asserts none survive in the data."""
    import psycopg
    with psycopg.connect(DSN) as c:
        bad = c.execute("""
            SELECT m.id, m.matched_text
              FROM claim_subject_mentions m JOIN claims cl ON cl.id = m.claim_id
             WHERE (m.span_start > 0
                    AND substring(cl.verbatim from m.span_start for 1) ~ '[[:alnum:]]'
                    AND left(m.matched_text, 1) ~ '[[:alnum:]]')
                OR (m.span_end < length(cl.verbatim)
                    AND substring(cl.verbatim from m.span_end + 1 for 1) ~ '[[:alnum:]]'
                    AND right(m.matched_text, 1) ~ '[[:alnum:]]')
        """).fetchall()
    assert bad == [], f"spans starting or ending mid-word: {bad[:5]}"
