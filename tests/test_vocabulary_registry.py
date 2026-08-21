"""Every `-- vocab: X` annotation in the DDL must name a real, populated vocabulary.

WHY THIS TEST EXISTS. Twice in one session I read a column name, queried vocabulary_terms
with it, got nothing back, and concluded the vocabulary was empty -- then seeded a parallel
set of terms beside the ones already there. `measure` is `quantity_measure`. `unit` is
`quantity_unit`. `asserted_precision` is `date_precision`. Of 59 vocabularies, 23 are named
differently from the column that uses them, so guessing the name from the column is wrong
by construction more than a third of the time.

An empty result from the wrong key is indistinguishable from an empty vocabulary. That is
this project's founding failure mode -- confidently wrong, nothing errors -- committed
against its own guardrail, and the fix is the one used everywhere else here: make the check
mechanical rather than remembered.
"""
import re
from pathlib import Path

import pytest

DDL = Path("schema/claim_store.sql")
SEEDS = [Path("schema/vocabularies.sql"), *sorted(Path("migrations").glob("*.sql"))]


def annotated_columns() -> list[tuple[str, str]]:
    """(column, vocabulary) for every `-- vocab: X` in the DDL."""
    s = DDL.read_text()
    return [(c, v.split("(")[0].strip())
            for c, v in re.findall(r"^\s*(\w+)\s+[A-Z][A-Z0-9_() ]*.*?--\s*vocab:\s*([\w ]+)",
                                   s, re.M)]


def declared_vocabularies() -> set[str]:
    """Vocabulary names any seed or migration inserts into `vocabularies`."""
    # Scanned rather than block-parsed. The seed is one INSERT of sixty rows with comments
    # between them, and a non-greedy match to the terminating semicolon stops at the first
    # one inside a description string. What is reliable is the row shape itself.
    out: set[str] = set()
    for p in SEEDS:
        if not p.exists():
            continue
        s = p.read_text()
        if "INSERT INTO vocabularies" not in s:
            continue
        after = s.split("INSERT INTO vocabularies", 1)[1]
        out |= set(re.findall(r"^\s*\(\s*'([\w]+)'\s*,", after, re.M))
    return out


def test_the_ddl_annotates_a_meaningful_number_of_columns():
    assert len(annotated_columns()) > 50


def test_every_annotated_vocabulary_is_actually_declared():
    """A column pointing at a vocabulary nobody creates is a silent free-text column."""
    declared = declared_vocabularies()
    missing = sorted({v for _, v in annotated_columns() if v not in declared})
    assert not missing, (
        f"{len(missing)} column(s) name a vocabulary that no seed or migration creates: "
        f"{missing}")


def test_the_column_name_is_not_a_reliable_guess_for_the_vocabulary_name():
    """Pinning the fact that burned me, so nobody re-derives it the hard way."""
    pairs = {(c, v) for c, v in annotated_columns()}
    differing = {(c, v) for c, v in pairs if c != v}
    assert ("measure", "quantity_measure") in differing
    assert ("asserted_precision", "date_precision") in differing
    assert len(differing) > 15, "if this shrank, the mapping got easier -- update the docs"


@pytest.mark.parametrize("column,vocabulary", sorted(set(annotated_columns())))
def test_each_vocabulary_has_at_least_one_term(column, vocabulary):
    """A declared vocabulary with no terms accepts anything and means nothing."""
    seeded = "".join(p.read_text() for p in SEEDS if p.exists())
    assert re.search(rf"\(\s*'{re.escape(vocabulary)}'\s*,\s*'", seeded), (
        f"vocabulary '{vocabulary}' (used by column '{column}') has no seeded terms")
