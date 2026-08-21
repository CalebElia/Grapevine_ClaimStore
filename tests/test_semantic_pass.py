"""The judgement pass must not be able to put a word in the document."""
from pipeline.semantic_pass import anchor_links, resolve_hyphens
import pipeline.semantic_pass as sp


def _stub(monkeypatch, reply):
    monkeypatch.setattr(sp, "_ask", lambda *a, **k: reply)


def test_a_hyphen_decision_is_one_of_exactly_two_strings(monkeypatch):
    _stub(monkeypatch, [{"a": "Collab", "b": "orator", "choice": "join", "why": "one word"}])
    out = resolve_hyphens([("Collab", "orator")], {})
    assert out[0]["resolved"] == "Collaborator"


def test_a_third_answer_is_discarded(monkeypatch):
    """The model chooses between two strings; it never supplies text."""
    _stub(monkeypatch, [{"a": "Collab", "b": "orator", "choice": "Collaborator!",
                         "why": "invented"}])
    assert resolve_hyphens([("Collab", "orator")], {}) == []


def test_a_case_that_was_not_asked_about_is_discarded(monkeypatch):
    _stub(monkeypatch, [{"a": "zero", "b": "waste", "choice": "join"}])
    assert resolve_hyphens([("Collab", "orator")], {}) == []


def test_a_link_sentence_must_be_verbatim(monkeypatch):
    text = "We also point out areas where more work is needed. Visit our site."
    _stub(monkeypatch, [{"uri": "http://x", "sentence": "Visit our site.", "why": "cites"}])
    out = anchor_links([{"uri": "http://x", "page_no": 3, "located": False}], text)
    assert out and out[0]["located"] and text[out[0]["char_start"]:out[0]["char_end"]] \
        == "Visit our site."


def test_a_paraphrased_sentence_is_rejected(monkeypatch):
    """A tidied quote is not located, and the link stays exactly as unanchored as it was."""
    text = "We also point out areas where more work is needed. Visit our site."
    _stub(monkeypatch, [{"uri": "http://x", "sentence": "Please visit our website.",
                         "why": "paraphrase"}])
    assert anchor_links([{"uri": "http://x", "page_no": 3, "located": False}], text) == []


def test_an_empty_sentence_means_no_citing_sentence_exists(monkeypatch):
    _stub(monkeypatch, [{"uri": "http://x", "sentence": "", "why": "navigation link"}])
    assert anchor_links([{"uri": "http://x", "page_no": 1, "located": False}], "text") == []


def test_nothing_to_do_costs_nothing(monkeypatch):
    assert resolve_hyphens([], {}) == [] and anchor_links([], "text") == []


def test_a_model_anchor_never_overwrites_a_string_matched_one():
    """A deterministic anchor is evidence; a model's is a proposal. Keying the merge on
    (uri, page) alone let an unanchored duplicate's answer replace its anchored twin --
    six links on the first real run, two to a materially different sentence."""
    from pipeline.semantic_pass import merge_anchored
    links = [{"uri": "http://x", "page_no": 3, "located": True,
              "context_sentence": "The sentence string matching found."},
             {"uri": "http://x", "page_no": 3, "located": False, "context_sentence": ""}]
    out = merge_anchored(links, [{"uri": "http://x", "page_no": 3, "located": True,
                                  "anchored_by": "semantic_pass",
                                  "context_sentence": "What the model proposed."}])
    assert out[0]["context_sentence"] == "The sentence string matching found."
    assert out[0].get("anchored_by") is None            # evidence is untouched
    assert out[1]["context_sentence"] == "What the model proposed."


def test_an_unmatched_link_is_left_exactly_as_it_was():
    from pipeline.semantic_pass import merge_anchored
    links = [{"uri": "http://y", "page_no": 9, "located": False}]
    assert merge_anchored(links, []) == links
