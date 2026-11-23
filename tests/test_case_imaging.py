"""Image-first binding of a case's fixed reports to real films (util/case_imaging).

The report is written FROM the film: a rewrite is accepted only if it still
retrieves the same film and mentions everything the film is known to show;
otherwise the original text stands and the case still loads.
"""
import json
import re

import pytest

from util import case_imaging as ci
from util import image_bank as ib

_PNG = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                     "01f15c4890000000a49444154789c63000100000500010d0a2db40000"
                     "000049454e44ae426082")


def _bank(tmp_path, entries):
    for e in entries:
        p = tmp_path / e["file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_PNG)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path)


def _ecg(ident, finding, umbrella=(), co=(), *, age=60, sex="M"):
    e = {"id": ident, "file": "ecg/%s.png" % ident, "modality": "ECG", "normality": "abnormal",
         "findings": [finding], "umbrella_terms": list(umbrella), "disease_keywords": [],
         "verified": True, "age": age, "sex": sex, "label_verification": "cardiologist-validated",
         "annotation": {"source": "ptbxl", "axis_zh": "電軸左偏",
                        "statements": [{"code": "X", "description": finding, "kind": "diagnostic",
                                        "likelihood": 100.0, "zh": finding + "(zh)"}]}}
    if co:
        e["co_findings"] = list(co)
        e["co_findings_validated"] = True
    e["attributes"] = ib.claim_attributes(*ib.claim_terms(e))
    return e


def _case(report):
    return {"基本資訊": {"姓名": "X", "年齡": 60, "性別": "男"},
            "MH": {"主訴": "胸痛"},
            "Problem": {"疾病": "急性心肌梗塞", "檢查預期所見": {"心電圖": report}}}


REPORT = "心電圖：II、III、aVF 導程 ST 段上升。符合急性下壁心肌梗塞。\n[ABNORMAL]"


def test_binding_without_a_rewriter_records_the_film(tmp_path):
    d = _bank(tmp_path, [_ecg("imi", "inferior myocardial infarction", ["myocardial infarction"])])
    data = _case(REPORT)
    b = ci.bind_case_to_films(data, directory=d)
    assert b["心電圖"]["target_id"] == "imi" and b["心電圖"]["rewritten"] is False
    assert data["Problem"]["檢查預期所見"]["心電圖"] == REPORT     # untouched
    assert data["_image_binding"]["心電圖"]["target_id"] == "imi"


def test_no_film_means_no_binding_and_no_change(tmp_path):
    d = _bank(tmp_path, [_ecg("af", "atrial fibrillation")])
    data = _case(REPORT)
    b = ci.bind_case_to_films(data, directory=d)
    assert b["心電圖"]["target_id"] is None
    assert data["Problem"]["檢查預期所見"]["心電圖"] == REPORT


def test_rewrite_is_kept_only_when_it_covers_the_films_findings(tmp_path):
    film = _ecg("imi", "inferior myocardial infarction", ["myocardial infarction"],
                co=["left anterior fascicular block"])
    d = _bank(tmp_path, [film])
    data = _case(REPORT)
    prompts = []

    def good_rewriter(prompt):
        prompts.append(prompt)
        # covers the co-finding the original omitted; keeps the inferior localization
        return ("心電圖：II、III、aVF 導程 ST 段上升，電軸左偏，合併左前分支阻滯。"
                "符合急性下壁心肌梗塞。\n[ABNORMAL]")

    b = ci.bind_case_to_films(data, rewrite=good_rewriter, directory=d)
    assert b["心電圖"]["rewritten"] is True and b["心電圖"]["unmentioned"] == []
    assert "左前分支阻滯" in data["Problem"]["檢查預期所見"]["心電圖"]
    # the prompt told the rewriter exactly what the original had missed
    assert "left anterior fascicular block" in prompts[0] or "左前分支阻滯" in prompts[0]


def test_a_rewrite_that_drifts_to_another_film_is_rejected(tmp_path):
    d = _bank(tmp_path, [
        _ecg("imi", "inferior myocardial infarction", ["myocardial infarction"], co=["left anterior fascicular block"]),
        _ecg("asmi", "anteroseptal myocardial infarction", ["myocardial infarction"]),
    ])
    data = _case(REPORT)
    bad = lambda p: "心電圖：V1–V4 ST 段上升，左前分支阻滯。符合急性前間壁心肌梗塞。\n[ABNORMAL]"
    b = ci.bind_case_to_films(data, rewrite=bad, retries=0, directory=d)
    assert b["心電圖"]["target_id"] == "imi" and b["心電圖"]["rewritten"] is False
    assert data["Problem"]["檢查預期所見"]["心電圖"] == REPORT
    assert "asmi" in b["心電圖"]["reason"]


def test_a_rewrite_that_still_omits_a_finding_is_retried_then_rejected(tmp_path):
    film = _ecg("imi", "inferior myocardial infarction", ["myocardial infarction"],
                co=["left anterior fascicular block"])
    d = _bank(tmp_path, [film])
    data = _case(REPORT)
    calls = []
    lazy = lambda p: (calls.append(p) or REPORT)       # never adds the missing finding
    b = ci.bind_case_to_films(data, rewrite=lazy, retries=1, directory=d)
    assert len(calls) == 2                                # one retry with feedback
    assert b["心電圖"]["rewritten"] is False and "omits" in b["心電圖"]["reason"]
    assert data["Problem"]["檢查預期所見"]["心電圖"] == REPORT


def test_rewrite_cannot_flip_the_normality_tag(tmp_path):
    d = _bank(tmp_path, [_ecg("imi", "inferior myocardial infarction", ["myocardial infarction"])])
    data = _case(REPORT)
    flip = lambda p: "心電圖：II、III、aVF 導程 ST 段上升。符合急性下壁心肌梗塞。\n[NORMAL]"
    ci.bind_case_to_films(data, rewrite=flip, directory=d)
    assert data["Problem"]["檢查預期所見"]["心電圖"].rstrip().endswith("[ABNORMAL]")


def test_a_crashing_rewriter_never_blocks_the_case(tmp_path):
    d = _bank(tmp_path, [_ecg("imi", "inferior myocardial infarction", ["myocardial infarction"])])
    data = _case(REPORT)
    boom = lambda p: (_ for _ in ()).throw(RuntimeError("quota"))
    b = ci.bind_case_to_films(data, rewrite=boom, directory=d)
    assert b["心電圖"]["target_id"] == "imi" and "quota" in b["心電圖"]["reason"]


def test_prompt_json_strips_bookkeeping_but_keeps_the_fixed_reports():
    data = _case(REPORT)
    data["_template_meta"] = {"x": 1}
    data["_image_binding"] = {"心電圖": {"target_id": "imi"}}
    text = ci.prompt_json(data)
    assert "_template_meta" not in text and "_image_binding" not in text
    assert "檢查預期所見" in text and "[ABNORMAL]" in text


def test_cases_without_fixed_reports_are_left_alone(tmp_path):
    d = _bank(tmp_path, [])
    data = {"基本資訊": {"年齡": 40, "性別": "女"}, "Problem": {"疾病": "x"}}
    assert ci.bind_case_to_films(data, directory=d) == {}
    assert "_image_binding" not in data


def test_annotation_brief_lists_what_the_film_shows():
    film = _ecg("imi", "inferior myocardial infarction", ["myocardial infarction"],
                co=["left anterior fascicular block"])
    brief = ib.annotation_brief_zh(film)
    assert "inferior myocardial infarction(zh)" in brief
    assert "left anterior fascicular block" in brief
    assert "電軸左偏" in brief
