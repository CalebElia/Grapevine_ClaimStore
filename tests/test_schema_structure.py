"""Structural validation of the DDL.

No Postgres is available in this environment, so these tests CANNOT confirm the SQL
executes. What they do check is a set of correctness properties specific to this
schema that a generic SQL linter would miss — above all, that the semi-open ontology
is actually wired up end to end:

    column comment  ->  seeded vocabulary  ->  seeded terms  ->  attached trigger

Every one of those four links has to hold or Rule 1 is decorative. A `-- vocab: X`
comment with no seeded vocabulary means the trigger silently does nothing; a seeded
vocabulary with no trigger means unapproved terms land unlogged.

Run: python -m pytest tests/ -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).parent.parent / "schema"
DDL = (SCHEMA_DIR / "claim_store.sql").read_text()
VOCAB = (SCHEMA_DIR / "vocabularies.sql").read_text()

# Constraint keywords that start a table-body line but are not column definitions.
_NOT_A_COLUMN = {
    "primary", "foreign", "unique", "check", "constraint", "exclude", "like",
}


def strip_line_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def parse_tables(ddl: str) -> dict[str, str]:
    """Return {table_name: raw body between the outermost parens}."""
    tables: dict[str, str] = {}
    for m in re.finditer(r"CREATE TABLE (\w+)\s*\(", ddl):
        name = m.group(1)
        depth, i = 1, m.end()
        while depth and i < len(ddl):
            if ddl[i] == "(":
                depth += 1
            elif ddl[i] == ")":
                depth -= 1
            i += 1
        tables[name] = ddl[m.end(): i - 1]
    return tables


def parse_columns(body: str) -> list[str]:
    cols = []
    for line in strip_line_comments(body).splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split()[0].lower().rstrip(",")
        if first in _NOT_A_COLUMN:
            continue
        if re.match(r"^\w+$", line.split()[0].rstrip(",")):
            cols.append(line.split()[0].rstrip(","))
    return cols


TABLES = parse_tables(DDL)
COLUMNS = {t: parse_columns(b) for t, b in TABLES.items()}

# {(table, column): vocabulary} from trailing `-- vocab: name` comments
COLUMN_VOCABS: dict[tuple[str, str], str] = {}
for _table, _body in TABLES.items():
    for _line in _body.splitlines():
        m = re.match(r"\s*(\w+)\s+[A-Za-z].*?--\s*vocab:\s*(\w+)", _line)
        if m:
            COLUMN_VOCABS[(_table, m.group(1))] = m.group(2)

SEEDED_VOCABS: dict[str, str | None] = {}
# Terminate on `);` at end of line — only the final VALUES row has that. Matching to
# the first bare `;` broke on a semicolon inside a description string, which parsed
# the block as empty and made three downstream tests pass over an empty set.
_vocab_block = re.search(
    r"INSERT INTO vocabularies.*?VALUES(.*?\));\s*\n", VOCAB, re.S
)
for m in re.finditer(
    r"\('([a-z_]+)',\s*'((?:[^']|'')*)',\s*(TRUE|FALSE),\s*(NULL|'[a-z_]+')\)",
    _vocab_block.group(1),
):
    fb = m.group(4)
    SEEDED_VOCABS[m.group(1)] = None if fb == "NULL" else fb.strip("'")

SEEDED_TERMS: set[tuple[str, str]] = set(
    re.findall(r"\('([a-z_]+)','([A-Za-z0-9_]+)','[^']*'\)", VOCAB)
)

TRIGGERS = re.findall(
    r"CREATE TRIGGER \w+\s+BEFORE INSERT OR UPDATE ON (\w+)\s+"
    r"FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary\('([a-z_]+)','(\w+)'",
    VOCAB,
)


# --------------------------------------------------------------------- sanity
# These guard the FIXTURES, not the schema. Without them an empty parse makes the
# vocabulary tests below pass vacuously — which is exactly what happened once.
def test_fixtures_parsed_non_empty():
    assert len(SEEDED_VOCABS) >= 50, f"parsed only {len(SEEDED_VOCABS)} vocabularies"
    assert len(SEEDED_TERMS) >= 250, f"parsed only {len(SEEDED_TERMS)} terms"
    assert len(TRIGGERS) >= 50, f"parsed only {len(TRIGGERS)} triggers"
    assert len(COLUMN_VOCABS) >= 50, f"parsed only {len(COLUMN_VOCABS)} vocab columns"


def test_ddl_parses_into_tables():
    assert len(TABLES) > 40, f"only found {len(TABLES)} tables"
    for required in ("claims", "claim_reasons", "asserted_events", "quantities",
                     "considered_alternatives", "vocabulary_terms", "source_search_log"):
        assert required in TABLES, f"{required} missing from DDL"


def test_parens_balanced():
    stripped = strip_line_comments(DDL)
    assert stripped.count("(") == stripped.count(")"), "unbalanced parentheses in DDL"
    v = strip_line_comments(VOCAB)
    assert v.count("(") == v.count(")"), "unbalanced parentheses in vocabularies.sql"


# ------------------------------------------------------- the Rule 1 chain
def test_every_column_vocab_reference_is_seeded():
    """A `-- vocab: X` comment with no seeded vocabulary means the trigger no-ops."""
    missing = {
        f"{t}.{c} -> {v}"
        for (t, c), v in COLUMN_VOCABS.items()
        if v not in SEEDED_VOCABS
    }
    assert not missing, f"vocabularies referenced but never seeded: {sorted(missing)}"


def test_every_seeded_vocabulary_is_used():
    """A seeded vocabulary nothing references is dead weight and will drift."""
    used = set(COLUMN_VOCABS.values()) | {v for _, v, _ in TRIGGERS}
    assert not (set(SEEDED_VOCABS) - used), \
        f"seeded but unused: {sorted(set(SEEDED_VOCABS) - used)}"


def test_every_vocabulary_has_terms():
    empty = {v for v in SEEDED_VOCABS if not any(t == v for t, _ in SEEDED_TERMS)}
    assert not empty, f"vocabularies with no approved terms: {sorted(empty)}"


def test_fallback_terms_are_themselves_approved():
    """The trigger substitutes fallback_term. If it isn't approved, the next write
    of that same row re-proposes it — an infinite proposal loop."""
    bad = {
        f"{v} -> {fb}"
        for v, fb in SEEDED_VOCABS.items()
        if fb is not None and (v, fb) not in SEEDED_TERMS
    }
    assert not bad, f"fallback_term not in vocabulary_terms: {sorted(bad)}"


def test_triggers_reference_real_tables_and_columns():
    problems = []
    for table, vocab, column in TRIGGERS:
        if table not in TABLES:
            problems.append(f"trigger on unknown table {table}")
        elif column not in COLUMNS[table]:
            problems.append(f"{table}.{column} does not exist")
        if vocab not in SEEDED_VOCABS:
            problems.append(f"trigger uses unseeded vocabulary {vocab}")
    assert not problems, problems


def test_every_vocab_column_has_a_trigger():
    """A vocabulary column with no trigger accepts unapproved terms silently —
    the exact failure mode Rule 1 exists to prevent."""
    attached = {(t, c) for t, _, c in TRIGGERS}
    unattached = set(COLUMN_VOCABS) - attached
    assert not unattached, \
        f"vocab columns with no enforcement trigger: {sorted(unattached)}"


# ------------------------------------------------------------ provenance
def test_verbatim_columns_reject_empty_strings():
    """NOT NULL accepts ''. v1's assembler wrote exactly that for 71 turns with no
    transcript text, and every one received a fabricated claim. The CHECK is the
    schema-level answer, so it must exist on every table carrying a verbatim."""
    missing = [
        t for t, body in TABLES.items()
        if "verbatim" in COLUMNS[t]
        and "length(trim(verbatim)) > 0" not in body
    ]
    # `decisions.verbatim` is nullable by design (a decision may be recorded from
    # structured Legistar fields with no quotable text), so it is exempt.
    missing = [t for t in missing if t != "decisions"]
    assert not missing, f"tables with verbatim but no non-empty CHECK: {missing}"


def test_claims_requires_a_source():
    body = TABLES["claims"]
    assert "utterance_id IS NOT NULL OR document_id IS NOT NULL" in body


def test_utterances_have_a_unique_natural_key():
    """v1 hashed start times into 10-second buckets; 1,156 turns collapsed to 631
    ids and 45% of turns received another turn's extraction. This constraint turns
    that class of bug into a database error."""
    assert "UNIQUE (media_asset_id, sequence)" in TABLES["utterances"]


# ------------------------------------------------------------------ time
def test_claims_carry_world_time_separate_from_utterance_time():
    cols = COLUMNS["claims"]
    for c in ("asserted_start", "asserted_end", "asserted_precision",
              "asserted_calendar", "asserted_date_text"):
        assert c in cols, f"claims.{c} missing — timeline generation needs it"


def test_documents_distinguish_three_dates():
    cols = COLUMNS["documents"]
    for c in ("published_date", "covers_period_start", "covers_period_end", "retrieved_at"):
        assert c in cols, f"documents.{c} missing"


def test_argument_propagation_view_does_not_use_retrieved_at():
    """v0.1 fell back to the scrape date, timestamping every document-sourced claim
    at the moment we happened to download it. That made temporal lag uncomputable."""
    view = re.search(r"CREATE VIEW v_argument_propagation AS(.*?);", DDL, re.S).group(1)
    assert "retrieved_at" not in view
    assert "published_date" in view and "asserted_start" in view


# ------------------------------------------------------- referential order
def test_foreign_keys_reference_tables_defined_earlier():
    """Postgres requires the target to exist. body_lineage.authorizing_matter_id is
    the one deliberate forward reference and is added by a later ALTER."""
    order = list(TABLES)
    problems = []
    for i, table in enumerate(order):
        defined = set(order[:i + 1])
        for target in re.findall(r"REFERENCES (\w+)\s*\(", TABLES[table]):
            if target not in defined:
                problems.append(f"{table} -> {target}")
    assert not problems, f"forward FK references: {problems}"


def test_privacy_gate_on_voiceprints_exists():
    """Persistent voiceprinting of private residents who speak at public comment is
    a different act from doing it for elected officials."""
    assert "reject_private_voiceprint" in DDL
    assert "trg_voiceprint_privacy" in DDL
    assert "is_public_figure" in DDL


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_a_human_verdict_must_name_its_author_and_pin_its_text():
    """CHECK sections_human_verdict_attributed. A verdict with no author is an anonymous
    gate pass; one with no hash cannot lapse when the conversion moves, and a stale
    approval lets extraction spend money on text nobody actually read."""
    ddl = open("migrations/014_human_verdict.sql").read()
    assert "sections_human_verdict_attributed" in ddl
    assert "human_verdict_by IS NOT NULL" in ddl
    assert "human_verdict_hash IS NOT NULL" in ddl
