

def test_a_reviewed_checklist_is_never_overwritten(tmp_path):
    """A checklist is derived until a human ticks a box in it; after that it is the only
    record of what they have already looked at. A shell redirect destroyed exactly that,
    with processing/ untracked and no snapshot to restore from."""
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "REVIEW_CHECKLIST.md"
    p.write_text("- [x] `y1.md:12` — checked, fine\n- [ ] `y1.md:20` — not yet\n")
    assert write_checklist(p, "- [ ] brand new\n") is False
    assert "- [x]" in p.read_text()                      # the human's copy is untouched
    assert (tmp_path / "REVIEW_CHECKLIST.regenerated.md").read_text() == "- [ ] brand new\n"


def test_an_arrow_note_alone_also_protects_the_file(tmp_path):
    """An untickeed item plus one note is still someone's working copy. The note has to
    OPEN its line -- a mid-line arrow is the generator's own item text."""
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y2.md:9` — heading looks wrong\n    → should be STRATEGY 3\n")
    assert write_checklist(p, "new\n") is False
    assert "→" in p.read_text()


def test_an_untouched_checklist_regenerates_normally(tmp_path):
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y1.md:3` — nothing reviewed here yet\n")
    assert write_checklist(p, "- [ ] fresh\n") is True
    assert p.read_text() == "- [ ] fresh\n"


def test_the_generators_own_arrows_do_not_trip_the_guard(tmp_path):
    """"AZERO → A2ZERO" is item text the generator writes itself. Matching the arrow
    anywhere made the guard refuse a pristine file, and a guard that cries wolf gets
    turned off."""
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y2.md` — OCR term correction applied 9×: AZERO → A2ZERO\n")
    assert write_checklist(p, "fresh\n") is True
    assert p.read_text() == "fresh\n"


def test_a_note_on_its_own_line_still_protects(tmp_path):
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y2.md` — something\n    → Should be deleted.\n")
    assert write_checklist(p, "fresh\n") is False
