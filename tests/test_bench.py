"""Benchmark harness. Tests the SCORER, which is the part that has to be right.

A vendor adapter failing is loud — an exception, a skipped row. A metric being subtly
wrong is silent: it produces a plausible number that decides a purchase. So these pin
the scoring behaviour, especially the cases where a metric could flatter or punish a
vendor for the wrong reason.
"""
from __future__ import annotations

import pytest

from pipeline.bench import metrics as M
from pipeline.bench.types import BenchTranscript, Turn, Word


def tx(turns, audio=60.0, **kw):
    kw.setdefault("has_word_timestamps", True)
    kw.setdefault("has_word_speakers", True)
    kw.setdefault("names_speakers", False)
    return BenchTranscript("t", "m", turns, audio, 1.0, **kw)


# ── word ──────────────────────────────────────────────────────────────────────

def test_wer_ignores_filler():
    """Vendors differ on whether they emit 'uh'. Punishing that measures transcription
    style, not accuracy."""
    assert M.wer("the plan is on track", "uh the plan is um on track")["wer"] == 0.0


def test_wer_counts_real_errors():
    r = M.wer("the A2Zero goal for 2050", "the A20 goal for 2015")
    assert r["wer"] > 0 and r["substitutions"] == 2


# ── entities ──────────────────────────────────────────────────────────────────

def test_entity_accuracy_catches_silent_failure():
    """The failure that matters: fluent text, wrong proper noun, zero errors visible."""
    ents = {"A2Zero": ["A20"]}
    assert M.entity_accuracy("the A20 goal and the A20 plan", ents)["entity_accuracy"] == 0.0
    assert M.entity_accuracy("the A2Zero goal", ents)["entity_accuracy"] == 1.0


def test_case_variant_in_mangle_list_does_not_self_penalise():
    """'Arca' as a listed misspelling of 'ARCA' collides after lowercasing and would
    score every CORRECT rendering as an error. Found while scoring the incumbent."""
    r = M.entity_accuracy("the ARCA agreement, the ARCA", {"ARCA": ["Arca", "Arcka"]})
    assert r["entity_accuracy"] == 1.0


# ── speaker ───────────────────────────────────────────────────────────────────

REF = [M.RefSegment(0, 10, "Ann"), M.RefSegment(10, 20, "Bob"),
       M.RefSegment(20, 30, "Ann")]


def test_der_is_zero_for_a_perfect_but_differently_labelled_diarization():
    """Cluster ids are arbitrary. A system that is perfect but calls Ann 'SPEAKER_07'
    must score 0, or the metric is measuring label strings."""
    t = tx([Turn("SPEAKER_07", 0, 10, "a"), Turn("SPEAKER_02", 10, 20, "b"),
            Turn("SPEAKER_07", 20, 30, "c")], audio=30)
    assert M.diarization_error(t, REF)["der"] == pytest.approx(0.0, abs=0.02)


def test_der_penalises_confusion():
    t = tx([Turn("S0", 0, 30, "everything")], audio=30)
    assert M.diarization_error(t, REF)["der"] > 0.25


def test_cluster_health_flags_a_junk_cluster():
    """SPEAKER_11 in the real meeting: fragments spanning several people, which S4
    confidently named. Low purity is the automatic tell."""
    t = tx([Turn("JUNK", 5, 15, "x"), Turn("S1", 20, 30, "y")], audio=30)
    h = M.cluster_health(t, REF)
    assert any(c["cluster"] == "JUNK" and c["purity"] < 0.8 for c in h["impure_clusters"])


def test_cluster_health_flags_a_split_speaker():
    t = tx([Turn("A", 0, 5, "x"), Turn("B", 5, 10, "y"), Turn("C", 10, 20, "z")], audio=30)
    assert any(s["speaker"] == "Ann" for s in M.cluster_health(t, REF)["split_speakers"])


def test_naming_is_none_when_vendor_does_not_name():
    t = tx([Turn("S0", 0, 10, "x")], names_speakers=False)
    assert M.naming(t, {"Ann"})["accuracy"] is None


def test_naming_matches_on_surname_not_spelling():
    """'Council Member Malik' vs roster 'Jon Mallek' is a registry lookup problem, not
    a diarization failure, and must not be scored as one."""
    t = tx([Turn("x", 0, 10, "hi", speaker_name="Jon Mallek")], names_speakers=True)
    assert M.naming(t, {"Jon Mallek", "Ann Smith"})["accuracy"] == 1.0


# ── turns ─────────────────────────────────────────────────────────────────────

def test_fragment_rate_catches_the_defect_we_actually_have():
    """67% of our interjections carried nothing usable. That is pure review cost."""
    t = tx([Turn("A", 0, 0.2, "ok"), Turn("A", 1, 1.1, ""),
            Turn("B", 2, 20, " ".join(["word"] * 60))], audio=30)
    q = M.turn_quality(t)
    assert q["fragment_turns"] == 1 and q["empty_turns"] == 1


def test_welded_turn_is_detected():
    """The expensive error: one turn containing two people. A claim inherits the
    turn's speaker, so this misattributes everything inside it."""
    t = tx([Turn("A", 0, 20, "spans Ann and Bob")], audio=30)
    assert M.turn_quality(t, REF)["welded_turns"] == 1


def test_turn_quality_needs_no_reference():
    """Intrinsic by design — scoring turns against our own segmentation would reward
    imitating the incumbent, which is the opposite of the question."""
    assert M.turn_quality(tx([Turn("A", 0, 10, "a b c")]))["turns"] == 1


# ── timestamps ────────────────────────────────────────────────────────────────

def test_timestamp_offset_measured_against_reference():
    ref = [("hello", 1.0), ("world", 2.0)]
    t = tx([Turn("A", 1.1, 2.1, "hello world",
                 [Word("hello", 1.1, 1.5), Word("world", 2.1, 2.5)])])
    r = M.timestamp_accuracy(t, ref)
    assert r["median_offset_s"] == pytest.approx(0.1, abs=0.01)


def test_interpolated_timestamps_are_flagged():
    """A vendor with no word timings gets its words spread evenly across the turn.
    That is an estimate and must never be reported as a measurement."""
    t = tx([Turn("A", 0, 4, "one two three four")], has_word_timestamps=False)
    assert M.timestamp_accuracy(t, [("one", 0.0)])["interpolated"] is True


# ── the objective ─────────────────────────────────────────────────────────────

def test_review_burden_prefers_fewer_edits():
    """The headline number must rank a structurally clean transcript above a
    word-accurate but shattered one — that is the entire point of the exercise."""
    clean = M.review_burden({"wer": 0.02}, {"clusters_found": 3, "split_speakers": []},
                            {"fragment_turns": 0, "empty_turns": 0, "welded_turns": 0},
                            {"names_speakers": True, "named": 3, "correct": 3},
                            {"entities_fully_mangled": 0})
    messy = M.review_burden({"wer": 0.005}, {"clusters_found": 21,
                            "split_speakers": [1, 2, 3]},
                            {"fragment_turns": 40, "empty_turns": 33, "welded_turns": 16},
                            {"names_speakers": False},
                            {"entities_fully_mangled": 2})
    assert clean["estimated_review_seconds"] < messy["estimated_review_seconds"]
    assert clean["estimated_review_seconds"] == 0


def test_coverage_reports_holes():
    t = tx([Turn("A", 0, 5, "x"), Turn("A", 40, 60, "y")], audio=60)
    c = M.coverage(t)
    assert c["gaps_over_5s"] == 1 and c["largest_gap_s"] == 35.0


# ── harness wiring ────────────────────────────────────────────────────────────

def test_every_vendor_with_a_key_slot_has_an_adapter():
    """ElevenLabs and Rev.ai were listed in KEY_ENV with no implementation, so a user
    who added those keys would have seen them silently never run. `--vendors all`
    iterates ADAPTERS, so the omission produced no error — just absent results."""
    from pipeline.bench import adapters as A
    missing = sorted(set(A.KEY_ENV) - set(A.ADAPTERS))
    assert not missing, f"key slot with no adapter: {missing}"


def test_model_names_are_env_overridable():
    """A hardcoded model name goes stale and then loses a bake-off for the wrong
    reason. Every vendor must be steerable from .env without editing code."""
    from pipeline.bench import adapters as A
    import os
    for v in A.ADAPTERS:
        if v == "local":
            continue
        assert v in A.MODEL_ENV, f"{v} has no model env var"
        assert A.model_for(v), f"{v} has no default model"
    os.environ["DEEPGRAM_MODEL"] = "nova-99"
    try:
        assert A.model_for("deepgram") == "nova-99"
        assert A.model_for("deepgram", "explicit") == "explicit"   # arg wins
    finally:
        del os.environ["DEEPGRAM_MODEL"]


def test_regional_endpoints_default_but_are_overridable():
    from pipeline.bench import adapters as A
    import os
    assert A.base_url("assemblyai").startswith("https://")
    os.environ["ASSEMBLYAI_BASE_URL"] = "https://api.eu.assemblyai.com"
    try:
        assert A.base_url("assemblyai") == "https://api.eu.assemblyai.com"
    finally:
        del os.environ["ASSEMBLYAI_BASE_URL"]


def test_env_example_documents_every_key_and_model_slot():
    """The template is the only instruction the user gets. If a slot exists in code and
    not in .env.example, it is effectively undiscoverable."""
    from pathlib import Path
    from pipeline.bench import adapters as A
    txt = (Path(__file__).parent.parent / ".env.example").read_text()
    for v, env in A.KEY_ENV.items():
        if env and v != "openai":
            assert env in txt, f"{env} missing from .env.example"
    for v, env in A.MODEL_ENV.items():
        assert env in txt, f"{env} missing from .env.example"


# ── vendor API contracts, learned the hard way ────────────────────────────────

def test_dashboard_display_names_normalize_to_api_ids():
    """Three of five preflight failures on the first live run were this. A user reads
    'Nova-3' or 'Universal-3.5 Pro' off a pricing page — which is what the docs tell
    them to do — and the API rejects it. Deepgram's rejection was actively misleading:
    "`keyterm` is only supported for Nova-3" while we were asking for exactly 'Nova-3',
    because its matcher is case-sensitive."""
    from pipeline.bench import adapters as A
    assert A.normalize_model("deepgram", "Nova-3") == "nova-3"
    assert A.normalize_model("assemblyai", "Universal-3.5 Pro") == "universal-3-5-pro"
    assert A.normalize_model("revai", "Speech-to-Text v1") == "machine"


def test_normalization_leaves_case_sensitive_ids_alone():
    """ElevenLabs model ids are lowercase-with-underscore and must survive intact —
    slugging them to 'scribe-v2' would break a vendor that currently works."""
    from pipeline.bench import adapters as A
    assert A.normalize_model("elevenlabs", "scribe_v2") == "scribe_v2"


def test_explain_surfaces_the_response_body():
    """httpx.HTTPStatusError stringifies to a URL and an MDN link. The body is where
    the API says what it actually rejected; the first version threw it away and turned
    ten-second fixes into documentation hunts."""
    import httpx
    from pipeline.bench.preflight import explain
    req = httpx.Request("POST", "https://example.test/v1")
    resp = httpx.Response(400, json={"error": "the model parameter is deprecated"},
                          request=req)
    msg = explain(httpx.HTTPStatusError("boom", request=req, response=resp))
    assert "deprecated" in msg and "400" in msg


def test_revai_cannot_be_given_terms_containing_digits():
    """A real vendor limitation, not a config error. Rev.ai rejects any custom-vocabulary
    phrase with a digit — which excludes 'A2Zero', the single most load-bearing entity
    name in this corpus and the exact term that started this line of work. The benchmark
    must record that Rev.ai ran without the one hint that matters most."""
    terms = ["A2Zero", "DTE", "Bryant", "Scio Township"]
    usable = [t for t in terms if not any(c.isdigit() for c in t)]
    assert "A2Zero" not in usable and "DTE" in usable


def test_silence_cannot_win_entity_accuracy():
    """A vendor that never produces the term at all must not outscore one that gets it
    right. Rev.ai did exactly this on the live run — 0 correct, 0 known-mangled for
    'A2Zero' — and took first place on entity accuracy by abstaining. Gold counts from
    the verified passage are what close it."""
    ents = {"A2Zero": ["A20"]}
    said_twice = {"A2Zero": 2}
    silent = M.entity_accuracy("the goal for 2030 is ambitious", ents, said_twice)
    right = M.entity_accuracy("the A2Zero goal and the A2Zero plan", ents, said_twice)
    assert silent["entity_accuracy"] == 0.0
    assert right["entity_accuracy"] == 1.0
    assert silent["per_entity"]["A2Zero"]["basis"] == "verified_count"


def test_der_miss_means_silence_not_an_unmapped_cluster():
    """A system that splits one speaker across two clusters must be charged CONFUSION
    for the second, not MISS. Conflating them produced a ~50% miss rate for six
    commercial diarizers simultaneously — when everything looks broken, the ruler is."""
    ref = [M.RefSegment(0, 10, "Ann")]
    split = tx([Turn("A", 0, 5, "x"), Turn("B", 5, 10, "y")], audio=10)
    r = M.diarization_error(split, ref, collar=0.0)
    assert r["missed"] == 0.0 and r["confusion"] > 0


def test_coalesce_makes_turn_granularity_comparable():
    """Vendors disagree on what a 'turn' is. Deepgram returned 53 turns for a single
    3-minute monologue where AssemblyAI, ElevenLabs and Rev.ai returned 2 — its
    utterances are sentence-level. Scored raw, Deepgram looked catastrophically
    fragmented for making a defensible segmentation choice."""
    sentences = tx([Turn("A", float(i), float(i) + 0.9, "a sentence here")
                    for i in range(10)], audio=10)
    merged = M.coalesce(sentences)
    assert len(merged.turns) == 1
    assert M.turn_quality(merged)["fragment_rate"] == 0.0


def test_coalesce_does_not_merge_across_speakers():
    t = tx([Turn("A", 0, 1, "x"), Turn("B", 1, 2, "y"), Turn("A", 2, 3, "z")], audio=3)
    assert len(M.coalesce(t).turns) == 3


def test_coalesce_respects_the_gap_tolerance():
    far = tx([Turn("A", 0, 1, "x"), Turn("A", 30, 31, "y")], audio=31)
    assert len(M.coalesce(far, gap=2.0).turns) == 2
