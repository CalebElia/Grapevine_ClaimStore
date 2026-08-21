"""The parse audit is a gate, so its failures must be the loud kind.

Every check here is anchored to a MEASURED failure in the real corpus, not to a hypothetical.
The A2Zero Year 2 report is the reference disaster: 14 pages, 47-120 embedded images per page,
19-157 characters of extractable text per page, 234 words total, and ZERO of its 16 dollar
figures. pdfplumber returns that and exits 0. Nothing raises. These tests exist so that stays
detected.

The first version of this audit used table-row census as the primary detector, on the theory
that `find_tables()` would see the known grant table even when text extraction mangled it.
Measured against the actual PDF that was FALSE -- the grant page yields zero detected tables,
while other pages report 18-49 spurious "rows" that are really bounding boxes around graphics.
Image-to-character ratio is what actually separates that document, so it is the primary check
and these tests pin it there.

Synthetic PageStat rows are used deliberately: the audit's logic must be testable without the
PDFs, which live in a read-only sibling repo and are 4-14 MB each.
"""

from __future__ import annotations

from pipeline.parse_audit import PageStat, conservation, tier1, verdict


def page(n, words=250, chars=1400, images=2, tables=0, rows=0, money=0, pct=0):
    return PageStat(page=n, words=words, chars=chars, images=images, tables=tables,
                    table_rows=rows, money=money, percents=pct)


def fired(flags, check):
    return [f for f in flags if f.check == check]


# ── the Year 2 disaster ──────────────────────────────────────────────────────────────────

def test_image_dominated_pages_are_flagged_high():
    """Measured Year 2 page 5: 120 images, 137 chars. Must fire, at high severity."""
    flags = tier1([page(5, words=27, chars=137, images=120)])
    hits = fired(flags, "image_dominance")
    assert hits and hits[0].severity == "high"
    assert "120 images" in hits[0].detail


def test_whole_document_of_image_pages_is_unusable_not_merely_suspect():
    """The distinction that keeps a reviewer's attention worth something.

    Year 2 flags 14 of 14 pages; Years 4 and 5 flag only their cover. A verdict that calls
    both 'suspect' is useless, so scale is part of the verdict.
    """
    stats = [page(i, words=20, chars=100, images=60) for i in range(1, 15)]
    v, frac = verdict(tier1(stats), len(stats))
    assert v == "unusable"
    assert frac >= 0.5


def test_isolated_cover_page_is_suspect_not_unusable():
    """Years 4 and 5: one graphic cover page in 24. Real, but not a broken document."""
    stats = [page(1, words=13, chars=57, images=5)] + [page(i) for i in range(2, 25)]
    v, frac = verdict(tier1(stats), len(stats))
    assert v == "suspect"
    assert frac < 0.5


def test_document_with_no_numerics_and_thin_pages_is_flagged():
    """Year 2 yielded zero dollar figures out of 16. That must be a finding on its own."""
    stats = [page(i, words=20, chars=30, images=50) for i in range(1, 15)]
    assert fired(tier1(stats), "no_numerics")


# ── the ordinary cases ───────────────────────────────────────────────────────────────────

def test_healthy_document_fires_nothing():
    stats = [page(i) for i in range(1, 11)]
    flags = tier1(stats)
    assert flags == []
    assert verdict(flags, len(stats))[0] == "clean"


def test_density_outlier_uses_the_documents_own_median():
    """A short page is only suspicious relative to its own document, not an absolute."""
    stats = [page(i, words=300, chars=1800) for i in range(1, 10)]
    stats.append(page(10, words=20, chars=1800))     # chars high, so image checks stay quiet
    assert fired(tier1(stats), "density_outlier")


def test_a_uniformly_short_document_is_not_flagged_for_density():
    """Every page short but consistent: that is a small report, not a parse failure."""
    stats = [page(i, words=40, chars=900) for i in range(1, 8)]
    assert not fired(tier1(stats), "density_outlier")


# ── numeric conservation ─────────────────────────────────────────────────────────────────

def test_conservation_reports_figures_dropped_from_the_conversion():
    raw = "Awarded $4,500,000 from ARPA and $75,000 from the McKnight Foundation. Up 12.5%."
    got = "Awarded $4,500,000 from ARPA."
    flags = conservation(raw, got)
    lost = fired(flags, "money_dropped")
    assert lost and "$75,000" in lost[0].evidence["lost"]
    assert fired(flags, "percent_dropped")


def test_conservation_is_silent_when_nothing_was_lost():
    raw = "Secured $2.5 million for housing."
    assert conservation(raw, "Secured $2.5 million for housing, per the report.") == []


def test_conservation_is_blind_on_image_only_pdfs_and_that_is_recorded():
    """A REAL limitation, pinned so nobody mistakes silence here for a clean parse.

    Conservation compares the conversion against the PDF's own text layer. Year 2 has
    essentially no text layer, so there is nothing to have lost -- the check reports nothing
    while 16 dollar figures sit unreachable inside images. Only image_dominance catches that,
    which is why it is the primary detector and why Tier 3 (vision census) exists.
    """
    assert conservation("", "some converted text") == []


# ── verdict discipline ───────────────────────────────────────────────────────────────────

def test_verdict_fails_closed_on_medium_only_findings():
    """Medium flags still deny 'clean'. Unproven is never treated as fine."""
    stats = [page(i, words=300, chars=1800) for i in range(1, 10)] + [page(10, words=20, chars=1800)]
    assert verdict(tier1(stats), len(stats))[0] == "suspect"


def test_empty_pdf_is_high_severity_not_clean():
    assert verdict(tier1([]), 0)[0] != "clean"


# ── orphaned list markers: the check that counts what is missing ──────────────────────────

def test_orphaned_bullets_report_how_many_items_are_unreadable():
    """Measured on Year 2 page 13 -- the grant page.

    Its ENTIRE extractable text is "• o o o o o o o o o o o o": one bullet and twelve
    sub-bullets, matching the twelve known grants, while every grant's content is an image.
    This is the only check that reports a COUNT of what was lost rather than merely that
    something is wrong, which is what makes it actionable for a reviewer.
    """
    stats = [page(13, words=13, chars=54, images=47)]
    stats[0].bullet_glyphs, stats[0].content_tokens = 13, 0
    hits = fired(tier1(stats), "orphan_list_markers")
    assert hits and hits[0].severity == "high"
    assert hits[0].evidence["bullet_glyphs"] == 13
    assert "13 list items" in hits[0].detail


def test_a_normal_bulleted_page_is_not_flagged():
    """Real lists have content beside their markers. Verified: zero hits across years 1,3,4,5."""
    s = page(4, words=180, chars=1200)
    s.bullet_glyphs, s.content_tokens = 8, 172
    assert not fired(tier1([s]), "orphan_list_markers")


def test_a_couple_of_stray_glyphs_do_not_trip_it():
    s = page(4, words=6, chars=900)
    s.bullet_glyphs, s.content_tokens = 2, 4
    assert not fired(tier1([s]), "orphan_list_markers")


# ── the silent-skip trap ─────────────────────────────────────────────────────────────────

def test_subthreshold_images_are_surfaced_before_they_are_silently_dropped():
    """Docling skips pictures under 5% of page area by default and says nothing.

    Measured on Year 2 page 13: 46 of 47 images fall below it, median area 0.0032. The
    default config would process one image and skip forty-six -- including the ones holding
    the grant text. A default value that discards content without erroring is exactly the
    failure class this project exists to catch, so the audit names it up front.
    """
    s = page(13, words=13, chars=54, images=47)
    s.small_images = 46
    hits = fired(tier1([s]), "subthreshold_images")
    assert hits and hits[0].evidence["small_images"] == 46
    assert "skipped silently" in hits[0].detail


def test_pages_with_a_few_large_images_are_not_flagged():
    s = page(4, images=6)
    s.small_images = 1
    assert not fired(tier1([s]), "subthreshold_images")
