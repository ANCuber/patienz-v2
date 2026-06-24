"""Synonym/plural/中英 tolerant matching for grading transparency."""
from util.grading_normalize import canonical, terms_match, match_against_standard


def test_plural_and_abbreviation_collapse():
    # The exact feedback: "DMARD vs DMARDs 差一個 s 就沒分"
    assert terms_match("DMARD", "DMARDs")
    assert terms_match("NSAID", "NSAIDs")


def test_chinese_english_synonyms():
    assert terms_match("x光攝影", "x-ray")
    assert terms_match("X ray", "X光")
    assert terms_match("肺栓塞", "PE")
    assert terms_match("心電圖", "ECG")
    assert terms_match("類風濕因子", "RF")


def test_distinct_terms_do_not_match():
    assert not terms_match("NSAID", "DMARD")
    assert not terms_match("肺栓塞", "心肌梗塞")
    assert not terms_match("", "x-ray")


def test_short_abbreviations_do_not_substring_falsematch():
    # regression: 2-letter keys must not match via substring tolerance
    assert not terms_match("RF", "CRF")
    assert not terms_match("CT", "ECT")
    # but a real synonym of the same concept still matches (handled by table)
    assert terms_match("CT", "電腦斷層")
    assert terms_match("RF", "類風濕因子")


def test_canonical_depluralizes_unknown_terms():
    # even without a table entry, trailing plural 's' collapses
    assert canonical("widgets") == canonical("widget")


def test_match_against_standard_reports_coverage():
    standard = "NSAID、DMARDs、Methotrexate"
    student = "我給了 NSAIDs 和 DMARD"
    cover = match_against_standard(student, standard)
    by_std = {c["standard"]: c for c in cover}
    assert by_std["NSAID"]["covered"] is True
    assert by_std["DMARDs"]["covered"] is True
    assert by_std["Methotrexate"]["covered"] is False
