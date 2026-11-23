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


# ---- §6-A ECG-diagnosis synonym groups (dual-use with the image bank) ----
import pytest
from util.grading_normalize import surface_forms


@pytest.mark.parametrize("a,b", [
    ("心房撲動", "atrial flutter"),
    ("左束支傳導阻滯", "left bundle branch block"),
    ("右束支傳導阻滯", "right bundle branch block"),
    ("竇性心搏過速", "sinus tachycardia"),
    ("竇性心搏過緩", "sinus bradycardia"),
    ("房室傳導阻滯", "AV block"),
    ("左心室肥厚", "left ventricular hypertrophy"),
    ("預激症候群", "WPW"),
    ("心肌缺血", "myocardial ischemia"),
])
def test_ecg_synonyms_match(a, b):
    assert terms_match(a, b)


def test_ischemia_is_not_infarction():
    # ischemia and infarction are distinct concepts and must not collapse
    assert not terms_match("myocardial ischemia", "myocardial infarction")
    assert not terms_match("心肌缺血", "心肌梗塞")


def test_surface_forms_include_chinese_ecg_terms():
    assert "左束支傳導阻滯" in surface_forms("left bundle branch block")
    assert "心房撲動" in surface_forms("atrial flutter")
    assert "房室阻滯" in surface_forms("AV block")


# ---- §6-A CXR-finding synonym groups (NIH ChestX-ray14 image bank) ----
@pytest.mark.parametrize("zh,en", [
    ("肺不張", "atelectasis"),
    ("心臟肥大", "cardiomegaly"),
    ("肺實變", "consolidation"),
    ("肺水腫", "pulmonary edema"),
    ("肋膜積水", "pleural effusion"),
    ("肺氣腫", "pulmonary emphysema"),
    ("肺纖維化", "pulmonary fibrosis"),
    ("橫膈疝", "diaphragmatic hernia"),
    ("肺浸潤", "pulmonary infiltrate"),
    ("肺腫塊", "pulmonary mass"),
    ("肺結節", "pulmonary nodule"),
    ("肋膜增厚", "pleural thickening"),
    ("氣胸", "pneumothorax"),
    ("肺炎", "pneumonia"),
])
def test_cxr_synonyms_match(zh, en):
    assert terms_match(zh, en)


def test_cxr_surface_forms_reach_the_chinese_report():
    # image retrieval evidences a film by looking its finding's surface forms up
    # inside the (Chinese) examiner report, so the zh form must be in the group.
    assert "肺水腫" in surface_forms("pulmonary edema")
    assert "肋膜積水" in surface_forms("pleural effusion")
    assert "肺結節" in surface_forms("pulmonary nodule")


@pytest.mark.parametrize("risky", ["mass", "effusion", "emphysema", "hernia", "edema"])
def test_bare_ambiguous_english_is_not_a_cxr_surface_form(risky):
    # Retrieval does substring lookup, so a bare "mass" would fire on "massive
    # effusion", "effusion" on "pericardial effusion", "emphysema" on
    # "subcutaneous emphysema", "hernia" on "inguinal hernia" — each a
    # clinically different thing that must not surface a chest film.
    for concept in ("pulmonary mass", "pleural effusion", "pulmonary emphysema",
                    "diaphragmatic hernia", "pulmonary edema"):
        assert risky not in surface_forms(concept)


def test_pleural_and_pericardial_effusion_do_not_collapse():
    assert not terms_match("pleural effusion", "pericardial effusion")


def test_cxr_distinct_concepts_stay_distinct():
    assert not terms_match("pulmonary nodule", "pulmonary mass")
    assert not terms_match("consolidation", "atelectasis")
    assert not terms_match("pulmonary edema", "pulmonary emphysema")
