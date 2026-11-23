"""Mutually-exclusive attribute gates in util.image_bank (§6-A).

The bank shipped a film captioned "anteroseptal myocardial infarction" onto a
report describing II/III/aVF ST elevation: the umbrella term "myocardial
infarction" cleared the evidence gate while the caption asserted a wall the
report never named, and the demographic tiebreak then chose between films on age
alone. The wrong wall means the wrong artery and the wrong management, so these
tests pin the rules that stop it — a film may not assert a territory, side,
severity or lesion type the report contradicted, or never claimed at all.
"""
import json
import re

import pytest

from util import image_bank as ib

_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c63000100000500010d0a2db40000"
    "000049454e44ae426082")


def _write_bank(tmp_path, entries):
    for e in entries:
        p = tmp_path / e["file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_PNG_1x1)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8")
    return str(tmp_path)


def _ecg(finding, umbrella=(), *, id=None, age=60, sex="M", verified=True):
    """An ECG entry shaped exactly like the builder's output — same vocabulary,
    so the builder and the gate cannot drift apart."""
    ident = id or ("ecg-" + re.sub(r"[^a-z0-9]+", "-", finding.lower()).strip("-"))
    return {"id": ident, "file": "ecg/%s.png" % ident, "modality": "ECG",
            "normality": "abnormal", "findings": [finding],
            "umbrella_terms": list(umbrella), "attributes": ib.claim_attributes(finding),
            "disease_keywords": [], "verified": verified, "age": age, "sex": sex}


def _cxr(finding, *, id=None, laterality=None, age=60, sex="M"):
    ident = id or ("cxr-" + re.sub(r"[^a-z0-9]+", "-", finding.lower()).strip("-"))
    attrs = ib.claim_attributes(finding)
    attrs["laterality"] = laterality
    return {"id": ident, "file": "cxr/%s.png" % ident, "modality": "CXR",
            "normality": "abnormal", "findings": [finding], "umbrella_terms": [],
            "attributes": attrs, "disease_keywords": [], "verified": True,
            "age": age, "sex": sex}


def _normal_cxr():
    return {"id": "cxr-norm", "file": "cxr/cxr-norm.png", "modality": "CXR",
            "normality": "normal", "findings": ["normal chest"], "verified": True,
            "umbrella_terms": [], "disease_keywords": []}


MI = ("myocardial infarction",)
INFERIOR_REPORT = "心電圖：II、III、aVF 導程 ST 段上升 3 mm。符合急性下壁心肌梗塞。"


# ---------- territory vocabulary ----------
@pytest.mark.parametrize("text,walls", [
    ("急性下壁心肌梗塞", {"inferior"}),
    ("acute inferior STEMI", {"inferior"}),
    ("前間壁心肌梗塞", {"anterior", "septal"}),
    ("anteroseptal myocardial infarction", {"anterior", "septal"}),
    ("下側壁心肌梗塞", {"inferior", "lateral"}),
    ("II、III、aVF ST elevation", {"inferior"}),          # leads, not wall names
    ("V1-V4 導程 ST 段上升", {"anterior", "septal"}),      # a lead range expands
    ("I、aVL、V5、V6 T 波倒置", {"lateral"}),
    ("心房顫動，心室率 110 次/分", set()),                  # nothing to localize
])
def test_territory_is_read_from_wall_names_and_from_leads(text, walls):
    assert set(ib._territory_of(text)) == walls


def test_a_parenthetical_qualifier_stays_inside_its_clause(tmp_path):
    # 「第三度（完全性）房室傳導阻滯」 must still evidence the third-degree film,
    # and the film's co-labelled incomplete RBBB must not read as "incomplete"
    e = _ecg("third degree AV block", ("atrioventricular block",))
    e["co_findings"] = ["incomplete right bundle branch block"]
    e["co_findings_validated"] = True
    e["attributes"] = ib.claim_attributes(*ib.claim_terms(e))
    first = _ecg("first degree AV block", ("atrioventricular block",), id="ecg-1avb")
    d = _write_bank(tmp_path, [e, first])
    rep = "心電圖：P 波與 QRS 完全無關，心室率 40 次/分。符合第三度（完全性）房室傳導阻滯。"
    m = ib.find_image("ECG", True, report_text=rep, directory=d)
    assert m is not None and m["findings"] == ["third degree AV block"]


def test_an_interval_is_not_the_septum():
    # 「RR 間隔」 / 「PR 間隔」 are intervals; only 中隔 / 室間隔 name the septum
    assert set(ib._territory_of("RR 間隔絕對不規則，PR 間隔 180 ms")) == set()
    assert set(ib._territory_of("室間隔肥厚")) == {"septal"}
    assert set(ib._territory_of("前間壁")) == {"anterior", "septal"}


def test_validated_co_findings_widen_the_films_claim():
    e = _ecg("inferior myocardial infarction", MI)
    e["co_findings"] = ["anteroseptal myocardial infarction", "left anterior fascicular block"]
    e["co_findings_validated"] = True
    assert ib.validated_co_findings(e) == ["anteroseptal myocardial infarction",
                                          "left anterior fascicular block"]
    assert ib.claim_terms(e)[0] == "inferior myocardial infarction"
    # the derived territory is the union of every lesion statement on the film
    del e["attributes"]
    assert set(ib._entry_attributes(e)["territory"]) == {"inferior", "anterior", "septal"}
    # NIH co-labels are NLP-mined: never part of the claim
    n = _cxr("pleural effusion")
    n["co_findings"] = ["Atelectasis"]
    assert ib.validated_co_findings(n) == []


def test_leads_in_a_conduction_description_are_not_a_territory():
    # an LBBB morphology description names leads without describing ischemia
    rep = ("心電圖：竇性心律，電軸左偏。QRS 增寬達 150 ms，V1 呈 QS 型，"
           "V5–V6 及 I、aVL 呈寬而有切跡之 R 波，符合完全性左束支傳導阻滯。")
    assert set(ib._report_attributes(rep)["territory"]) == set()
    # ...but the same leads with an ST/Q description do localize
    rep2 = "心電圖：V1–V4 導程 ST 段上升並見病理性 Q 波。"
    assert set(ib._report_attributes(rep2)["territory"]) == {"anterior", "septal"}


def test_co_findings_do_not_change_the_films_own_completeness_or_lesion():
    # a third-degree AV block film that also carries an incomplete RBBB label
    a = ib.claim_attributes("third degree AV block", "incomplete right bundle branch block")
    assert a["completeness"] is None and a["lesion"] is None
    # ...but a co-labelled infarct does widen the territory the film shows
    b = ib.claim_attributes("left bundle branch block", "inferior myocardial infarction")
    assert b["territory"] == ["inferior"] and b["lesion"] is None


def test_a_cleaner_film_beats_one_with_unmentioned_validated_findings(tmp_path):
    clean = _ecg("inferior myocardial infarction", MI, id="ecg-clean", age=80)
    busy = _ecg("inferior myocardial infarction", MI, id="ecg-busy", age=60)
    busy["co_findings"] = ["anteroseptal myocardial infarction"]
    busy["co_findings_validated"] = True
    busy["attributes"] = ib.claim_attributes(*ib.claim_terms(busy))
    d = _write_bank(tmp_path, [clean, busy])
    # the busy film is demographically closer, but the student would see an
    # anteroseptal infarct the report never described
    m = ib.find_image("ECG", True, report_text=INFERIOR_REPORT, patient_age=60,
                      patient_sex="M", directory=d)
    assert m["id"] == "ecg-clean"
    # ...unless the report does describe it, in which case it is a fine example
    rep = INFERIOR_REPORT + " 另見 V1–V4 病理性 Q 波，符合陳舊性前間壁心肌梗塞。"
    m = ib.find_image("ECG", True, report_text=rep, patient_age=60,
                      patient_sex="M", directory=d)
    assert m["id"] == "ecg-busy"


def test_reciprocal_changes_are_not_a_second_territory():
    # aVL depression opposite an inferior STEMI marks the *opposite* wall;
    # counting it would turn a pure inferior infarct into an inferolateral one.
    rep = "II、III、aVF ST 段上升，aVL 呈對側性 ST 壓低。符合急性下壁心肌梗塞。"
    assert set(ib._report_attributes(rep)["territory"]) == {"inferior"}
    assert set(ib._territory_of(rep)) == {"inferior", "lateral"}   # unscoped, for contrast


def test_negated_clause_contributes_no_territory():
    rep = "心電圖：II、III、aVF ST 段上升。V1-V4 未見異常。符合下壁心肌梗塞。"
    assert set(ib._report_attributes(rep)["territory"]) == {"inferior"}


# ---------- R1: contradiction ----------
def test_a_film_of_a_different_wall_is_rejected(tmp_path):
    d = _write_bank(tmp_path, [_ecg("anteroseptal myocardial infarction", MI)])
    assert ib.find_image("ECG", True, report_text=INFERIOR_REPORT, directory=d) is None


def test_the_matching_wall_is_shown(tmp_path):
    d = _write_bank(tmp_path, [_ecg("inferior myocardial infarction", MI)])
    m = ib.find_image("ECG", True, report_text=INFERIOR_REPORT, directory=d)
    assert m["findings"] == ["inferior myocardial infarction"]


def test_overlapping_territory_is_allowed_but_the_exact_one_wins(tmp_path):
    d = _write_bank(tmp_path, [
        _ecg("inferolateral myocardial infarction", MI, id="ecg-ilmi", age=59),
        _ecg("inferior myocardial infarction", MI, id="ecg-imi", age=20),
    ])
    # the inferolateral film is demographically much closer, yet exactness wins
    m = ib.find_image("ECG", True, report_text=INFERIOR_REPORT, patient_age=60,
                      patient_sex="M", directory=d)
    assert m["id"] == "ecg-imi"


def test_an_overlapping_film_is_still_shown_when_it_is_all_there_is(tmp_path):
    d = _write_bank(tmp_path, [_ecg("inferolateral myocardial infarction", MI)])
    assert ib.find_image("ECG", True, report_text=INFERIOR_REPORT, directory=d) is not None


def test_complete_and_incomplete_block_do_not_substitute(tmp_path):
    d = _write_bank(tmp_path, [_ecg("complete right bundle branch block",
                                    ("right bundle branch block",))])
    assert ib.find_image("ECG", True, directory=d,
                         report_text="心電圖：不完全性右束支傳導阻滯。") is None
    assert ib.find_image("ECG", True, directory=d,
                         report_text="心電圖：完全性右束支傳導阻滯。") is not None


def test_injury_and_infarction_do_not_substitute(tmp_path):
    d = _write_bank(tmp_path, [_ecg("subendocardial injury in inferior leads", MI)])
    assert ib.find_image("ECG", True, report_text=INFERIOR_REPORT, directory=d) is None


def test_an_ischemia_report_does_not_surface_an_infarct_film(tmp_path):
    d = _write_bank(tmp_path, [_ecg("inferior myocardial infarction", MI)])
    rep = "心電圖：II、III、aVF T 波倒置。考慮下壁心肌缺血。"
    assert ib.find_image("ECG", True, report_text=rep, directory=d) is None


# ---------- R5: the film may not localize where the report did not ----------
def test_a_report_without_localization_does_not_get_a_localized_film(tmp_path):
    d = _write_bank(tmp_path, [_ecg("anteroseptal myocardial infarction", MI)])
    rep = "心電圖：ST 段上升合併 T 波變化，符合急性心肌梗塞變化。"
    assert ib.find_image("ECG", True, report_text=rep, directory=d) is None


def test_a_report_without_localization_still_gets_an_unlocalized_film(tmp_path):
    d = _write_bank(tmp_path, [_ecg("non-specific ischemic", ("myocardial ischemia",))])
    m = ib.find_image("ECG", True, report_text="心電圖：ST-T 變化，考慮心肌缺血。", directory=d)
    assert m is not None and m["findings"] == ["non-specific ischemic"]


# ---------- laterality: recorded by a reviewer, never by the dataset ----------
def test_the_wrong_side_is_rejected_once_the_side_is_recorded(tmp_path):
    d = _write_bank(tmp_path, [_cxr("pneumothorax", laterality="left")])
    assert ib.find_image("CXR", True, report_text="胸部X光：右側氣胸。", directory=d) is None


def test_the_matching_side_is_shown(tmp_path):
    d = _write_bank(tmp_path, [_cxr("pneumothorax", laterality="right")])
    assert ib.find_image("CXR", True, report_text="胸部X光：右側氣胸。", directory=d) is not None


def test_an_unannotated_film_is_not_rejected_for_side(tmp_path):
    # laterality stays None until a reviewer fills it in; the gate then has
    # nothing to compare, so the bank is not silently hidden in the meantime.
    d = _write_bank(tmp_path, [_cxr("pneumothorax", laterality=None)])
    assert ib.find_image("CXR", True, report_text="胸部X光：右側氣胸。", directory=d) is not None


def test_laterality_is_read_from_the_clause_that_evidences_the_finding(tmp_path):
    # the effusion is on the left; a right-sided pneumothorax named elsewhere in
    # the same report must not veto the left-sided effusion film.
    d = _write_bank(tmp_path, [_cxr("pleural effusion", laterality="left")])
    rep = "胸部X光：右側肺尖可見氣胸，左側肋膜積水。"
    assert ib.find_image("CXR", True, report_text=rep, directory=d) is not None


# ---------- R2 / R3 / R4: specificity, ranking, conclusion clause ----------
def test_an_umbrella_only_match_loses_to_a_specific_one(tmp_path):
    d = _write_bank(tmp_path, [
        _ecg("non-specific ST elevation", ("ST elevation",), id="ecg-ste", age=20),
        _ecg("inferior myocardial infarction", MI, id="ecg-imi", age=80),
    ])
    m = ib.find_image("ECG", True, report_text=INFERIOR_REPORT, patient_age=20,
                      patient_sex="M", directory=d)
    assert m["id"] == "ecg-imi"


def test_a_finding_mentioned_in_passing_does_not_beat_the_conclusion(tmp_path):
    # the reported defect, end to end: a 20-year-old woman, a sinus-tachycardia
    # film from a 19-year-old woman, and the real diagnosis in the last clause.
    d = _write_bank(tmp_path, [
        _ecg("sinus tachycardia", id="ecg-stach", age=19, sex="F"),
        _ecg("inferior myocardial infarction", MI, id="ecg-imi", age=49, sex="F"),
    ])
    rep = ("心電圖：竇性心搏過速，心率 110 次/分。II、III、aVF 導程 ST 段上升 3 mm。"
           "符合急性下壁心肌梗塞。")
    m = ib.find_image("ECG", True, report_text=rep, patient_age=20,
                      patient_sex="女", directory=d)
    assert m["id"] == "ecg-imi", "demographics must not promote an incidental finding"


def test_the_incidental_finding_is_still_served_when_it_is_the_conclusion(tmp_path):
    d = _write_bank(tmp_path, [_ecg("sinus tachycardia", age=30, sex="F")])
    m = ib.find_image("ECG", True, patient_age=31, patient_sex="女", directory=d,
                      report_text="心電圖：竇性心搏過速，心率 122 次/分，餘未見異常。")
    assert m is not None and m["findings"] == ["sinus tachycardia"]


@pytest.mark.parametrize("report,expected", [
    # clinical names nest, so both films match; the fuller wording must win
    ("心電圖：陣發性室上性心搏過速。", "paroxysmal supraventricular tachycardia"),
    ("心電圖：規則窄 QRS 心搏過速，符合室上性心搏過速。", "supraventricular tachycardia"),
    ("心電圖：非診斷性T波異常。", "non-diagnostic T abnormalities"),
    ("心電圖：T波異常。", "T-wave abnormality"),
])
def test_the_more_fully_spelled_out_finding_wins(tmp_path, report, expected):
    d = _write_bank(tmp_path, [
        _ecg("supraventricular tachycardia", id="ecg-svt", age=20),
        _ecg("paroxysmal supraventricular tachycardia", id="ecg-psvt", age=80),
        _ecg("T-wave abnormality", id="ecg-t", age=20),
        _ecg("non-diagnostic T abnormalities", id="ecg-ndt", age=80),
    ])
    m = ib.find_image("ECG", True, report_text=report, patient_age=20,
                      patient_sex="M", directory=d)
    assert m is not None and m["findings"] == [expected]


def test_without_a_conclusion_cue_the_last_clause_is_used():
    assert ib._conclusion_clauses("心電圖：心房顫動。心室率 110 次/分") == ["心室率 110 次/分"]
    assert ib._conclusion_clauses("A。符合 B。C") == ["符合 B", "C"]


def test_the_examiners_impression_line_counts_as_the_conclusion(tmp_path):
    # markdown report: label and verdict sit on either side of a colon
    rep = ("**[胸部X光] 檢查結果：**\n- **整體印象：** 右側大量肋膜積水\n"
           "- **具體發現：**\n  - 右側中下肺野均質緻密陰影\n  - 竇性心搏過速（見心電圖）\n"
           "- **重要陰性發現：** 未見氣胸\n[ABNORMAL]")
    assert "右側大量肋膜積水" in " ".join(ib._conclusion_clauses(rep))
    d = _write_bank(tmp_path, [_cxr("pleural effusion"), _cxr("pneumothorax", id="cxr-ptx")])
    m = ib.find_image("CXR", True, report_text=rep, directory=d)
    assert m is not None and m["findings"] == ["pleural effusion"]


# ---------- builder / manifest contract ----------
@pytest.mark.parametrize("text", ["ischemic in inferior leads", "myocardial ischemia",
                                  "ischaemia", "ischemia", "心肌缺血"])
def test_every_spelling_of_ischemia_is_recognised(text):
    # "isch[ae]emi" demanded a vowel the American spelling lacks, so the whole
    # ischemic family carried no lesion value and could not be told apart from
    # an infarct.
    assert ib._first_value(text, ib._LESION) == "ischemia"


@pytest.mark.parametrize("claim", ["left anterior fascicular block",
                                   "left posterior fascicular block",
                                   "septal hypertrophy"])
def test_a_wall_word_outside_a_lesion_statement_is_not_a_territory(claim):
    # These name a conduction path or a structure, not an infarct territory.
    # Treating them as localized made rule R5 hide them from every report that
    # did not happen to name a wall.
    assert ib.claim_attributes(claim)["territory"] == []


def test_a_fascicular_block_film_survives_a_report_with_no_wall(tmp_path):
    e = _ecg("left anterior fascicular block")
    e["findings"] = ["left anterior fascicular block"]
    d = _write_bank(tmp_path, [e])
    assert ib.find_image("ECG", True, directory=d,
                         report_text="ECG: left anterior fascicular block.") is not None


def test_claim_attributes_shape():
    assert ib.claim_attributes("inferior myocardial infarction") == {
        "territory": ["inferior"], "completeness": None,
        "lesion": "infarction", "laterality": None}
    assert ib.claim_attributes("complete left bundle branch block")["completeness"] == "complete"
    assert ib.claim_attributes("pleural effusion")["territory"] == []
    # never inferred from a label — only a reviewer may set it
    assert ib.claim_attributes("right pneumothorax")["laterality"] is None


def test_specific_and_umbrella_split():
    new = {"findings": ["inferior myocardial infarction"],
           "umbrella_terms": ["myocardial infarction"]}
    assert ib._specific_and_umbrella(new) == (["inferior myocardial infarction"],
                                              ["myocardial infarction"])
    legacy = {"findings": ["anteroseptal myocardial infarction", "myocardial infarction"]}
    assert ib._specific_and_umbrella(legacy) == (["anteroseptal myocardial infarction"],
                                                 ["myocardial infarction"])


def test_a_legacy_entry_without_the_new_fields_is_still_gated(tmp_path):
    legacy = {"id": "old-asmi", "file": "ecg/old-asmi.png", "modality": "ECG",
              "normality": "abnormal", "verified": True, "disease_keywords": [],
              "findings": ["anteroseptal myocardial infarction", "myocardial infarction"]}
    d = _write_bank(tmp_path, [legacy])
    assert ib.find_image("ECG", True, report_text=INFERIOR_REPORT, directory=d) is None
    ok = "心電圖：V1-V4 ST 段上升。符合前間壁心肌梗塞。"
    assert ib.find_image("ECG", True, report_text=ok, directory=d) is not None


def test_normal_retrieval_is_untouched_by_the_attribute_gates(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr()])
    assert ib.find_image("CXR", has_abnormal=False, item_terms=["Chest X-ray"],
                         directory=d) is not None


# ===================== the mismatch notice shown under the image ==============
# The gates above remove films that would mislead. What is left is the mismatch
# inherent to showing another real patient's film, and the student has to be
# told about it rather than left to infer it.

def _joined(entry, **kw):
    return "\n".join(ib.risk_notes(entry, **kw))


def test_every_image_says_it_is_not_this_patient():
    notes = ib.risk_notes(_ecg("atrial fibrillation"))
    assert notes and "另一位真實病人" in notes[0]


def test_no_entry_no_notes():
    assert ib.risk_notes(None) == []
    assert ib.risk_notes({}) == []


def test_a_generated_image_says_so_and_says_nothing_else():
    e = _ecg("atrial fibrillation")
    e["synthetic"] = True
    notes = ib.risk_notes(e)
    assert len(notes) == 1 and "AI 生成" in notes[0]


# ---------- side ----------
def test_an_unannotated_side_is_flagged():
    text = _joined(_cxr("pneumothorax", laterality=None),
                   report_text="胸部X光：右側氣胸。")
    assert "尚未經人工標註" in text and "請以文字報告的側別為準" in text


def test_a_recorded_side_is_stated_instead_of_warned_about():
    text = _joined(_cxr("pneumothorax", laterality="right"),
                   report_text="胸部X光：右側氣胸。")
    assert "右側" in text and "尚未經人工標註" not in text


def test_a_finding_with_no_side_gets_no_side_note():
    text = _joined(_cxr("cardiomegaly"), report_text="胸部X光：心臟肥大。")
    assert "側別" not in text and "尚未經人工標註" not in text


# ---------- territory ----------
def test_a_wider_territory_is_spelled_out():
    text = _joined(_ecg("inferolateral myocardial infarction", MI),
                   report_text=INFERIOR_REPORT)
    assert "下壁＋側壁" in text and "下壁" in text and "不完全相同" in text


def test_an_exact_territory_needs_no_note():
    text = _joined(_ecg("inferior myocardial infarction", MI), report_text=INFERIOR_REPORT)
    assert "不完全相同" not in text


# ---------- demographics ----------
def test_a_distant_patient_is_flagged_and_a_close_one_is_not():
    film = _ecg("atrial fibrillation", age=72, sex="F")
    far = _joined(film, patient_age=20, patient_sex="女")
    near = _joined(film, patient_age=70, patient_sex="女")
    assert "女性 72 歲" in far and "女性 20 歲" in far
    assert "本病人是" not in near


def test_a_different_sex_alone_is_enough_to_flag():
    text = _joined(_ecg("atrial fibrillation", age=60, sex="M"),
                   patient_age=60, patient_sex="女")
    assert "男性 60 歲" in text


def test_the_demographic_caveat_names_the_right_modality_baselines():
    ecg = _joined(_ecg("atrial fibrillation", age=70), patient_age=20, patient_sex="M")
    cxr = _joined(_cxr("cardiomegaly", age=70), patient_age=20, patient_sex="M")
    assert "QRS 電壓" in ecg and "心胸比" not in ecg.split("影像來自")[1].split("。")[0]
    assert "心胸比標準" in cxr


# ---------- what else may be on the film ----------
def test_unconfirmed_co_findings_are_named_as_not_the_patients():
    e = _cxr("pleural effusion")
    e["co_findings"] = ["Atelectasis", "Cardiomegaly"]
    text = _joined(e)
    assert "Atelectasis" in text and "不代表本虛擬病人也有" in text


def test_without_co_findings_the_generic_caveat_is_shown():
    text = _joined(_cxr("pleural effusion"))
    assert "報告沒提到的其他變化" in text


def test_measurements_are_never_to_be_read_off_the_image():
    assert "不要從圖上反推" in _joined(_ecg("atrial fibrillation"))


# ---------- who vouched for the label ----------
# A radiologist's bounding box is localization, not adjudication: one reader,
# no second review, drawn FOR a label they were already given, and the dataset
# records no case where a reader rejected one. It must not read as "verified".

def test_review_status_ranks_the_sources():
    assert ib.review_status(_cxr("pneumonia")) == "unknown"
    e = _cxr("pneumonia")
    e["label_verification"] = "nlp-mined"
    assert ib.review_status(e) == "nlp-mined"
    e["label_verification"] = "radiologist-bbox"
    assert ib.review_status(e) == "radiologist-bbox"
    e["clinician_reviewed"] = True          # a person here looked: outranks all
    assert ib.review_status(e) == "clinician"


def test_a_text_mined_label_is_disclosed_as_unchecked_by_any_doctor():
    e = _cxr("pneumonia")
    e["label_verification"] = "nlp-mined"
    text = _joined(e)
    assert "未經任何醫師確認" in text and "90%" in text and "不要當成標準答案" in text


def test_a_bounding_box_is_disclosed_as_localization_not_verification():
    e = _cxr("pneumonia")
    e["label_verification"] = "radiologist-bbox"
    text = _joined(e)
    assert "不是對標籤本身的驗證" in text
    assert "未經任何醫師確認" not in text        # it is a weaker warning, not the red one


def test_a_cardiologist_validated_label_says_so_without_alarm():
    e = _ecg("atrial fibrillation")
    e["label_verification"] = "cardiologist-validated"
    text = _joined(e)
    assert "心臟科醫師標註並經人工複核" in text
    assert "未經任何醫師確認" not in text and "不是對標籤本身的驗證" not in text


def test_a_locally_reviewed_film_carries_no_label_caveat():
    e = _cxr("pneumonia")
    e["label_verification"] = "nlp-mined"
    e["clinician_reviewed"] = True
    text = _joined(e)
    for phrase in ("未經任何醫師確認", "不是對標籤本身的驗證", "來源未記錄"):
        assert phrase not in text


def test_an_unrecorded_provenance_is_not_silently_trusted():
    assert "來源未記錄" in _joined(_cxr("pneumonia"))


def test_an_ap_film_warns_against_the_pa_cardiothoracic_ratio():
    e = _cxr("cardiomegaly")
    e["view_position"] = "AP"
    assert "AP" in _joined(e) and "心胸比標準" in _joined(e)
    e["view_position"] = "PA"
    assert "AP（前後位）" not in _joined(e)


# ---------------------------------------------------------------- reader verdict
def _with_read(entry, visible, *, concern="", treatment=False, devices=()):
    entry["annotation"] = {
        "reviewed_by": "ai-vision-3reader",
        "target": {"finding": entry["findings"][0], "visible": visible, "laterality": "right",
                   "zone": "lower", "size_cm": 5, "confidence": 0.5},
        "agreement": "full", "devices": [{"type": d, "location": "", "zh": d} for d in devices],
        "other_findings": [], "treatment_evidence": treatment, "label_concern": concern}
    return entry


def test_a_film_whose_finding_the_readers_could_not_see_is_never_shown(tmp_path):
    seen = _with_read(_cxr("pleural effusion", id="cxr-seen", laterality="right"), "yes")
    unseen = _with_read(_cxr("pleural effusion", id="cxr-unseen", laterality="right", age=50), "no",
                        concern="無肋膜積水徵象")
    d = _write_bank(tmp_path, [seen, unseen])
    # the unseen film is the closer demographic match, yet it loses
    got = ib.find_image("CXR", True, report_text="右側肋膜積水", patient_age=50, patient_sex="M", directory=d)
    assert got["id"] == "cxr-seen"
    # and with nothing else available it is not shown at all
    d2 = _write_bank(tmp_path / "b", [unseen])
    assert ib.find_image("CXR", True, report_text="右側肋膜積水", directory=d2) is None


def test_an_equivocal_film_ranks_below_a_clear_one_but_above_none(tmp_path):
    clear = _with_read(_cxr("pleural effusion", id="cxr-clear", laterality="right"), "yes")
    equivocal = _with_read(_cxr("pleural effusion", id="cxr-equivocal", laterality="right", age=50),
                           "uncertain", concern="僅見肋膈角輕度鈍化")
    d = _write_bank(tmp_path, [clear, equivocal])
    got = ib.find_image("CXR", True, report_text="右側肋膜積水", patient_age=50, patient_sex="M", directory=d)
    assert got["id"] == "cxr-clear"
    d2 = _write_bank(tmp_path / "b", [equivocal])
    got2 = ib.find_image("CXR", True, report_text="右側肋膜積水", directory=d2)
    assert got2["id"] == "cxr-equivocal"
    text = " ".join(ib.risk_notes(got2, report_text="右側肋膜積水"))
    assert "無法確認本片確有" in text and "僅見肋膈角輕度鈍化" in text


def test_clinician_signoff_outranks_the_ai_readers(tmp_path):
    e = _with_read(_cxr("pleural effusion", id="cxr-signed", laterality="right"), "no")
    e["clinician_reviewed"] = True
    d = _write_bank(tmp_path, [e])
    assert ib.target_visibility(e) == "yes"
    assert ib.find_image("CXR", True, report_text="右側肋膜積水", directory=d)["id"] == "cxr-signed"


def test_treatment_evidence_is_disclosed():
    e = _with_read(_cxr("pneumothorax", id="cxr-ptx", laterality="left"), "yes",
                   treatment=True, devices=("胸管",))
    text = " ".join(ib.risk_notes(e, report_text="左側氣胸"))
    assert "已有治療痕跡" in text and "胸管" in text
    e2 = _with_read(_cxr("pneumothorax", id="cxr-ptx2", laterality="left"), "yes")
    assert "已有治療痕跡" not in " ".join(ib.risk_notes(e2, report_text="左側氣胸"))


def test_reader_concern_on_a_visible_film_is_quoted_as_a_note():
    e = _with_read(_cxr("pulmonary nodule", id="cxr-nod", laterality="right"), "yes",
                   concern="結節密度高、疑鈣化，較可能為良性肉芽腫")
    text = " ".join(ib.risk_notes(e, report_text="右側肺結節"))
    assert "讀片備註" in text and "良性肉芽腫" in text and "無法確認本片確有" not in text


def test_an_impression_header_outranks_a_hedged_cue_in_the_findings(tmp_path):
    """研判 in a findings bullet must not promote an aside to principal."""
    pna = _cxr("pneumonia", id="cxr-pna", laterality="left", age=38)
    atx = _cxr("atelectasis", id="cxr-atx", laterality="right", age=38)
    d = _write_bank(tmp_path, [pna, atx])
    report = ("**[胸部X光] 檢查結果：**\n- **整體印象：** 左側肺炎\n- **具體發現：**\n"
              "  - 左中下肺野斑片狀浸潤，符合左側肺炎\n"
              "  - 右心緣旁淡薄模糊影，研判為橫膈上抬與心影重疊或輕度基底肺膨脹不全\n[ABNORMAL]")
    assert [c for c in ib._conclusion_clauses(report) if "肺膨脹不全" in c] == []
    got = ib.find_image("CXR", True, report_text=report, patient_age=38, patient_sex="M", directory=d)
    assert got["id"] == "cxr-pna"
    # without a header the softer cues still work
    assert any("符合" in c for c in ib._conclusion_clauses("左中下肺野斑片狀浸潤，符合左側肺炎。"))


def test_ecg_stadium_field_that_contradicts_the_cardiologist_is_disclosed():
    e = _ecg("inferior myocardial infarction", id="ecg-4166")
    e["label_verification"] = "cardiologist-validated"
    e["annotation"] = {"stadium_zh": "急性期", "source_report_zh": "竇性心律，下壁梗塞，可能為陳舊性。",
                       "report_consistent_with_codes": True, "report_issues": "stadium 欄位與報告矛盾"}
    text = " ".join(ib.risk_notes(e, report_text="陳舊性下壁心肌梗塞"))
    assert "分期欄位標為**急性期**" in text and "可能為陳舊性" in text
    # a mixed stadium ("亞急性期至陳舊性") agrees with "陳舊性" and is not flagged
    e["annotation"]["stadium_zh"] = "亞急性期至陳舊性"
    assert "分期欄位" not in " ".join(ib.risk_notes(e, report_text="陳舊性下壁心肌梗塞"))
    # a translation caveat alone is not a discrepancy; a coded one is
    assert "有出入" not in " ".join(ib.risk_notes(e))
    e["annotation"]["report_consistent_with_codes"] = False
    assert "有出入" in " ".join(ib.risk_notes(e))


def test_annotation_brief_carries_the_readers_verdict_and_caveats():
    cxr = _with_read(_cxr("pneumothorax", id="cxr-ptx", laterality="left"), "uncertain",
                     concern="未見明確臟層肋膜線", treatment=True, devices=("胸管",))
    brief = ib.annotation_brief_zh(cxr)
    assert "能見度：不確定" in brief and "標籤疑慮：未見明確臟層肋膜線" in brief and "治療痕跡：有" in brief
    ecg = _ecg("inferior myocardial infarction", id="ecg-x")
    ecg["annotation"] = {"statements": [], "report_issues": "stadium 欄位與報告矛盾"}
    assert "判讀核對備註：stadium 欄位與報告矛盾" in ib.annotation_brief_zh(ecg)


# ---------------------------------------------------------------- review findings
def test_the_side_note_names_only_the_sources_that_actually_agreed():
    """Claiming a radiologist box contributed when only AI readers did overstates
    the evidence on the axis that matters most."""
    both = _cxr("pleural effusion", id="cxr-both", laterality="right")
    both["attributes"]["laterality_source"] = "bbox+ai-vision"
    ai_only = _cxr("pleural effusion", id="cxr-ai", laterality="right")
    ai_only["attributes"]["laterality_source"] = "ai-vision"
    t_both = " ".join(ib.risk_notes(both, report_text="右側肋膜積水"))
    t_ai = " ".join(ib.risk_notes(ai_only, report_text="右側肋膜積水"))
    assert "放射科醫師定位框的位置與 AI 讀片" in t_both
    assert "僅由 AI 讀片推得" in t_ai and "沒有放射科醫師定位框" in t_ai
    assert "放射科醫師定位框的位置與 AI 讀片" not in t_ai


def test_bundle_branch_statements_are_in_the_vocabulary():
    """PTB-XL's own statement strings must be evidenceable in a zh-TW report,
    or the risk note tells the student the report omitted what it just said."""
    irbbb = ["incomplete right bundle branch block"]
    assert ib._evidence_count(irbbb, "QRS 約 120 ms，呈不完全性右束支傳導阻滯型態（V1 rSr'）")
    assert ib._evidence_count(["complete left bundle branch block"], "符合完全性左束支傳導阻滯")
    # and the degrees stay distinct: an incomplete report must not pull a
    # complete-block film (that is a different reading)
    assert not ib._contradicts(ib.claim_attributes(*irbbb),
                               ib._report_attributes("不完全性右束支傳導阻滯"), irbbb, "不完全性右束支傳導阻滯")
    assert ib._contradicts(ib.claim_attributes("complete right bundle branch block"),
                           ib._report_attributes("不完全性右束支傳導阻滯"),
                           ["complete right bundle branch block"], "不完全性右束支傳導阻滯")


def test_a_report_that_omits_the_degree_gets_no_degree_claiming_film(tmp_path):
    # the real bank gives both films the umbrella term "right bundle branch
    # block" (tools/build_ptbxl_bank.py), so a report that writes only the
    # umbrella can reach either one — which is exactly why the completeness
    # family has to decide, not the demographics
    rbbb = ("right bundle branch block",)
    crbbb = _ecg("complete right bundle branch block", rbbb, id="ecg-crbbb", age=45, sex="F")
    irbbb = _ecg("incomplete right bundle branch block", rbbb, id="ecg-irbbb", age=25, sex="M")
    d = _write_bank(tmp_path, [crbbb, irbbb])
    vague = "**[心電圖] 檢查結果：**\n- **整體印象：** 右束支傳導阻滯\n- **具體發現：**\n  - 竇性心律\n[ABNORMAL]"
    # demographics must not be what decides complete vs incomplete
    assert ib.find_image("ECG", True, report_text=vague, patient_age=25, patient_sex="M", directory=d) is None
    assert ib.find_image("ECG", True, report_text=vague, patient_age=45, patient_sex="F", directory=d) is None
    # the QRS duration is the actual criterion (>=120 ms = complete), so a report
    # that gives it does get a film
    wide = vague.replace("竇性心律", "竇性心律，QRS 增寬達 140 ms")
    got = ib.find_image("ECG", True, report_text=wide, patient_age=25, patient_sex="M", directory=d)
    assert got["id"] == "ecg-crbbb"
    narrow = vague.replace("竇性心律", "竇性心律，QRS 100 ms")
    got = ib.find_image("ECG", True, report_text=narrow, patient_age=45, patient_sex="F", directory=d)
    assert got["id"] == "ecg-irbbb"
    # a QRS duration outside a block discussion implies nothing
    assert ib._report_attributes("II、III、aVF 病理性 Q 波，QRS 90 ms，陳舊性下壁心肌梗塞")["completeness"] is None
