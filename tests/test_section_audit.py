"""The machine may only demote. A human's sign-off is necessary and not sufficient."""
import hashlib

from pipeline.section_audit import machine_verdict

BODY = "The Greenbelt reached 7,600 acres of farmland permanently protected."
H = hashlib.sha256(BODY.encode()).hexdigest()


def test_a_matching_section_with_no_flags_is_clean():
    conf, _ = machine_verdict(BODY, H, None)
    assert conf == "clean"


def test_a_conversion_that_moved_since_ingest_is_suspect():
    """A review of text that moved is not a review of the text in the store."""
    conf, why = machine_verdict(BODY + " and more.", H, None)
    assert conf == "suspect" and "conversion changed" in why


def test_a_garbled_token_is_suspect_however_confident_the_human_was():
    """This is the check that caught two sentences woven together AFTER a clean gate and a
    clean 135-item checklist. A human can miss it; the machine cannot."""
    body = "EnhaEnNciHngA NthCeE rTeHsiEli"
    conf, why = machine_verdict(body, hashlib.sha256(body.encode()).hexdigest(), None)
    assert conf == "suspect" and "garbled" in why


def test_a_demoting_flag_lowers_only_its_own_section():
    for flag in ("placement_inferred", "has_ocr_blocks"):
        conf, why = machine_verdict(BODY, H, {flag: True})
        assert conf == "suspect" and flag in why


def test_a_harmless_flag_does_not_demote():
    """has_captions and has_furniture describe TAGGED content, which is exactly what
    tagging was for -- they say a section contains something ingest can filter, not
    something the conversion got wrong."""
    conf, _ = machine_verdict(BODY, H, {"has_captions": True, "has_furniture": True})
    assert conf == "clean"


def test_an_empty_section_is_never_clean():
    conf, _ = machine_verdict("", hashlib.sha256(b"").hexdigest(), None)
    assert conf == "suspect"
