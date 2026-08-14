"""Real-image bank retrieval + pedagogical-safety gates (proposal §6-A).

The retrieval contract these tests pin down (post-adversarial-review): an image
is shown ONLY when it is human-verified, modality-matched, and — for abnormal
films — its own documented finding is evidenced in *this exam's report*, or —
for normal multi-anatomy films — its region matches the ordered study. Anything
short of that returns None and the page shows text only. Each confirmed review
finding has a named regression test below.
"""
import base64
import importlib.util
import json
import os

import pytest

import util.image_bank as ib
import util.grading_normalize as gn

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A real (tiny) 1x1 PNG so image_path()'s os.path.isfile + st.image would work.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _write_bank(tmp_path, entries, with_files=True):
    for e in entries:
        if with_files and e.get("file"):
            p = tmp_path / e["file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(_PNG_1x1)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(tmp_path)


# ---------- entry factories (verified by default = eligible to display) ----------
def _normal_cxr(id="cxr-n", verified=True):
    return {"id": id, "file": "cxr/%s.png" % id, "modality": "CXR", "normality": "normal",
            "findings": ["normal chest"], "disease_keywords": [], "verified": verified}


def _pneumonia_cxr(id="cxr-pna", verified=True):
    return {"id": id, "file": "cxr/%s.png" % id, "modality": "CXR", "normality": "abnormal",
            "findings": ["pneumonia", "consolidation"], "disease_keywords": ["pneumonia"],
            "verified": verified}


def _hemorrhage_ct(id="ct-ich", verified=True):
    return {"id": id, "file": "ct/%s.png" % id, "modality": "CT", "normality": "abnormal",
            "findings": ["intracranial hemorrhage"], "disease_keywords": ["intracranial hemorrhage"],
            "verified": verified}


# ---------- resolve_modality ----------
@pytest.mark.parametrize("texts,expected", [
    (("心電圖", "Electrocardiography"), "ECG"),
    (("X光", "Chest X-ray"), "CXR"),
    (("X光", "Abdominal X-ray"), "XR"),
    (("X光", "KUB"), "XR"),
    (("超音波", "Echocardiography"), "ECHO"),
    (("超音波", "Renal Ultrasound"), "US"),
    (("CT", "Chest CT"), "CT"),
    (("CT", "CT Angiography (CTA)"), "CT"),
    (("MRI", "Cardiac MRI"), "MRI"),
    (("MRI", "MRA"), "MRI"),
    (("內視鏡", "Colonoscopy"), "ENDO"),
    (("其他影像", "Bone Scan"), "NM"),
    (("動脈血分析", "pH pCO2 pO2"), None),   # blood gas is not imaging
    (("功能檢查", "Pulmonary Function Test (PFT)"), None),
    (("", ""), None),
])
def test_resolve_modality(texts, expected):
    assert ib.resolve_modality(*texts) == expected


# ---------- manifest loading ----------
def test_missing_manifest_is_empty(tmp_path):
    assert ib.load_manifest(str(tmp_path))["entries"] == []


def test_corrupt_manifest_is_empty(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    assert ib.load_manifest(str(tmp_path))["entries"] == []


def test_manifest_drops_unknown_modality(tmp_path):
    d = _write_bank(tmp_path, [
        {"id": "x", "file": "cxr/a.png", "modality": "BOGUS", "normality": "normal"},
        {"id": "y", "file": "cxr/b.png", "modality": "CXR", "normality": "normal"},
    ])
    ids = [e["id"] for e in ib.load_manifest(d)["entries"]]
    assert ids == ["y"]


# ---------- surface_forms (report-evidence vocabulary) ----------
def test_surface_forms_expands_concepts():
    assert "肺炎" in gn.surface_forms("pneumonia")
    assert "腦出血" in gn.surface_forms("intracranial hemorrhage")
    assert "心房顫動" in gn.surface_forms("atrial fibrillation")
    # a term with no synonym group falls back to itself
    assert gn.surface_forms("consolidation") == ["consolidation"]


# ---------- find_image: hard filters ----------
def test_modality_is_hard_filter(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr()])
    assert ib.find_image("ECG", has_abnormal=False, directory=d) is None


def test_none_modality_returns_none(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr()])
    assert ib.find_image(None, has_abnormal=False, directory=d) is None


def test_disabled_env_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PATIENZ_DISABLE_IMAGE_BANK", "1")
    d = _write_bank(tmp_path, [_normal_cxr()])
    assert ib.find_image("CXR", has_abnormal=False, directory=d) is None


def test_missing_file_is_skipped(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr()], with_files=False)
    assert ib.find_image("CXR", has_abnormal=False, directory=d) is None


# ---------- find_image: verified gate (finding F4) ----------
def test_unverified_abnormal_never_shown(tmp_path):
    d = _write_bank(tmp_path, [_pneumonia_cxr(verified=False)])
    assert ib.find_image("CXR", has_abnormal=True,
                         report_text="patchy consolidation, pneumonia", directory=d) is None


def test_unverified_normal_never_shown(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr(verified=False)])
    assert ib.find_image("CXR", has_abnormal=False, directory=d) is None


# ---------- find_image: normality direction ----------
def test_normal_single_region_matches_without_overlap(tmp_path):
    d = _write_bank(tmp_path, [_normal_cxr()])
    m = ib.find_image("CXR", has_abnormal=False, directory=d)
    assert m and m["id"] == "cxr-n"


def test_normal_report_never_returns_abnormal_image(tmp_path):
    d = _write_bank(tmp_path, [_pneumonia_cxr()])
    assert ib.find_image("CXR", has_abnormal=False, directory=d) is None


# ---------- find_image: abnormal gated on THIS report's finding (finding F1/F3) ----------
def test_abnormal_requires_report_evidence(tmp_path):
    d = _write_bank(tmp_path, [_pneumonia_cxr()])
    # report that does not mention the finding -> text only
    assert ib.find_image("CXR", has_abnormal=True,
                         report_text="Clear lung fields, no infiltrate.", directory=d) is None
    # report that evidences the film's finding -> shown
    m = ib.find_image("CXR", has_abnormal=True,
                      report_text="Patchy consolidation, consistent with pneumonia.", directory=d)
    assert m and m["id"] == "cxr-pna"


def test_abnormal_report_evidence_is_bilingual(tmp_path):
    d = _write_bank(tmp_path, [_pneumonia_cxr()])
    m = ib.find_image("CXR", has_abnormal=True, report_text="右下肺葉可見肺炎浸潤。", directory=d)
    assert m and m["id"] == "cxr-pna"


def test_ischemic_stroke_never_gets_hemorrhage_film(tmp_path):
    # Finding F1: matching must key off the report, not the case's headline disease.
    d = _write_bank(tmp_path, [_hemorrhage_ct()])
    ischemic = ib.find_image("CT", has_abnormal=True,
                             report_text="Acute infarct, left MCA territory; ischemic stroke.",
                             item_terms=["Head CT", "頭部電腦斷層"], directory=d)
    assert ischemic is None
    hemorrhagic = ib.find_image("CT", has_abnormal=True,
                                report_text="Hyperdense intracranial hemorrhage, right basal ganglia.",
                                directory=d)
    assert hemorrhagic and hemorrhagic["id"] == "ct-ich"


def test_acs_ecg_reporting_afib_is_not_shown_the_stemi_tracing(tmp_path):
    # Finding F1: two abnormal ECGs; the AFib report must not pull the STEMI film.
    stemi = {"id": "ecg-stemi", "file": "ecg/stemi.png", "modality": "ECG", "normality": "abnormal",
             "findings": ["ST elevation", "myocardial infarction"],
             "disease_keywords": ["acute coronary syndrome"], "verified": True}
    afib = {"id": "ecg-afib", "file": "ecg/afib.png", "modality": "ECG", "normality": "abnormal",
            "findings": ["atrial fibrillation"], "disease_keywords": ["atrial fibrillation"],
            "verified": True}
    d = _write_bank(tmp_path, [stemi, afib])
    m = ib.find_image("ECG", has_abnormal=True,
                      report_text="Irregularly irregular rhythm; atrial fibrillation with RVR.",
                      directory=d)
    assert m and m["id"] == "ecg-afib"


def test_highest_report_evidence_wins_deterministically(tmp_path):
    weak = {"id": "cxr-b", "file": "cxr/cxr-b.png", "modality": "CXR", "normality": "abnormal",
            "findings": ["consolidation"], "disease_keywords": [], "verified": True}
    strong = {"id": "cxr-a", "file": "cxr/cxr-a.png", "modality": "CXR", "normality": "abnormal",
              "findings": ["pneumonia", "consolidation"], "disease_keywords": ["pneumonia"],
              "verified": True}
    d = _write_bank(tmp_path, [weak, strong])
    m = ib.find_image("CXR", has_abnormal=True,
                      report_text="Consolidation with features of pneumonia.", directory=d)
    assert m["id"] == "cxr-a"  # 2 evidenced findings > 1


# ---------- find_image: normal multi-anatomy region gate (finding F2) ----------
def _normal_abdo_us(id="us-abdo"):
    return {"id": id, "file": "us/%s.png" % id, "modality": "US", "normality": "normal",
            "findings": ["normal abdomen"], "disease_keywords": [],
            "caption": "Normal abdominal ultrasound", "verified": True}


def test_normal_carotid_us_does_not_show_abdominal_film(tmp_path):
    d = _write_bank(tmp_path, [_normal_abdo_us()])
    assert ib.find_image("US", has_abnormal=False,
                         item_terms=["Carotid Ultrasound", "頸動脈超音波"], directory=d) is None


def test_normal_abdominal_us_shows_abdominal_film(tmp_path):
    d = _write_bank(tmp_path, [_normal_abdo_us()])
    m = ib.find_image("US", has_abnormal=False,
                      item_terms=["Abdominal Ultrasound", "腹部超音波"], directory=d)
    assert m and m["id"] == "us-abdo"


# ---------- describe ----------
def test_describe_real_image(tmp_path):
    d = _write_bank(tmp_path, [dict(_pneumonia_cxr(), source="NIH", license="CC0",
                                    attribution="NIH CC", source_url="http://x",
                                    caption="RLL consolidation")])
    entry = ib.load_manifest(d)["entries"][0]
    meta = ib.describe(entry, directory=d)
    assert meta["synthetic"] is False
    assert "真實" in meta["badge"]
    assert meta["caption"] == "RLL consolidation"
    assert "NIH" in meta["provenance"] and "CC0" in meta["provenance"]
    assert meta["path"] and os.path.isfile(meta["path"])


def test_describe_synthetic_badge(tmp_path):
    e = {"id": "derm", "file": "cxr/derm.png", "modality": "CXR", "normality": "abnormal",
         "findings": ["rash"], "synthetic": True, "verified": True}
    d = _write_bank(tmp_path, [e])
    meta = ib.describe(ib.load_manifest(d)["entries"][0], directory=d)
    assert meta["synthetic"] is True
    assert "生成" in meta["badge"]


# ---------- env-driven bank_dir integration ----------
def test_env_bank_dir_end_to_end(tmp_path, monkeypatch):
    d = _write_bank(tmp_path, [_normal_cxr()])
    monkeypatch.setenv("PATIENZ_IMAGE_BANK_DIR", d)
    monkeypatch.delenv("PATIENZ_DISABLE_IMAGE_BANK", raising=False)
    m = ib.find_image("CXR", has_abnormal=False)  # no directory arg → uses env
    assert m and m["id"] == "cxr-n"
    assert ib.image_path(m) and os.path.isfile(ib.image_path(m))


# ---------- fetcher license filter (finding F7) ----------
def _load_fetch_module():
    spec = importlib.util.spec_from_file_location(
        "fetch_image_bank_mod", os.path.join(_ROOT, "tools", "fetch_image_bank.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lic(short):
    return {"extmetadata": {"LicenseShortName": {"value": short}}}


@pytest.mark.parametrize("short,ok", [
    ("CC0", True),
    ("Public domain", True),
    ("CC BY 4.0", True),
    ("CC BY-SA 4.0", True),
    ("CC BY-NC 2.0", False),      # NonCommercial — not redistributable here
    ("CC BY-ND 4.0", False),      # NoDerivatives
    ("CC BY-NC-ND 4.0", False),
    ("CC BY-NC-SA 3.0", False),
    ("All rights reserved", False),
])
def test_license_filter_rejects_nc_nd(short, ok):
    fetch = _load_fetch_module()
    assert fetch._license_ok(_lic(short)) is ok


# ---------- demographic-proximity tiebreak (all candidates already safe) ----------
def _pna(id, age, sex):
    return dict(_pneumonia_cxr(id), age=age, sex=sex)


def test_demographic_tiebreak_prefers_closer(tmp_path):
    d = _write_bank(tmp_path, [_pna("cxr-a", 52, "M"), _pna("cxr-b", 80, "F")])
    rep = "Patchy consolidation, consistent with pneumonia."
    # patient near A (男 -> M, age 50)
    m = ib.find_image("CXR", has_abnormal=True, report_text=rep,
                      patient_age=50, patient_sex="男", directory=d)
    assert m["id"] == "cxr-a"
    # patient near B (女 -> F, age 78)
    m2 = ib.find_image("CXR", has_abnormal=True, report_text=rep,
                       patient_age=78, patient_sex="女", directory=d)
    assert m2["id"] == "cxr-b"


def test_no_demographics_is_deterministic_by_id(tmp_path):
    d = _write_bank(tmp_path, [_pna("cxr-b", 80, "F"), _pna("cxr-a", 52, "M")])
    m = ib.find_image("CXR", has_abnormal=True,
                      report_text="pneumonia with consolidation", directory=d)
    assert m["id"] == "cxr-a"  # demo distance 0 for all → id tiebreak


def test_evidence_score_beats_demographic_proximity(tmp_path):
    near = {"id": "cxr-near", "file": "cxr/cxr-near.png", "modality": "CXR",
            "normality": "abnormal", "findings": ["consolidation"], "verified": True,
            "age": 50, "sex": "M"}
    far = {"id": "cxr-far", "file": "cxr/cxr-far.png", "modality": "CXR",
           "normality": "abnormal", "findings": ["pneumonia", "consolidation"],
           "verified": True, "age": 85, "sex": "F"}
    d = _write_bank(tmp_path, [near, far])
    m = ib.find_image("CXR", has_abnormal=True, report_text="pneumonia with consolidation",
                      patient_age=50, patient_sex="M", directory=d)
    assert m["id"] == "cxr-far"  # 2 evidenced findings > 1, despite worse demographics
