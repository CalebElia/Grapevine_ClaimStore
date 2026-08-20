

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
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y2.md:9` — heading looks wrong → should be STRATEGY 3\n")
    assert write_checklist(p, "new\n") is False
    assert "→" in p.read_text()


def test_an_untouched_checklist_regenerates_normally(tmp_path):
    from pipeline.review_checklist import write_checklist
    p = tmp_path / "c.md"
    p.write_text("- [ ] `y1.md:3` — nothing reviewed here yet\n")
    assert write_checklist(p, "- [ ] fresh\n") is True
    assert p.read_text() == "- [ ] fresh\n"
