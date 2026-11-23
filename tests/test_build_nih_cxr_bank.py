"""Pure parsing/selection/coverage logic of tools/build_nih_cxr_bank.py (§6-A).

Image copying (filesystem/Pillow) is not exercised here; these tests pin the
decisions that determine *which* NIH ChestX-ray14 films become bank images and,
crucially, which of them are allowed to be ``verified`` — the flag that decides
whether ``util.image_bank.find_image`` may ever show one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_nih_cxr_bank as b  # noqa: E402


# The shipped default: chest-radiograph strata 0-18 / 19-39 / 40-59 / 60-79 / 80+.
DEFAULT_BOUNDS = [0, 19, 40, 60, 80]


def rec(labels, sex="M", age=50.0, view="PA", image="00000001_000.png",
        patient="1", follow_up=0):
    return {"image": image, "labels": set(labels), "follow_up": follow_up,
            "patient": patient, "age": age, "sex": sex, "view": view}


# ---------- age bands ----------
def test_default_bands_are_the_five_chest_strata():
    assert b.parse_bands("0,19,40,60,80") == DEFAULT_BOUNDS


@pytest.mark.parametrize("age,band", [
    (0.5, 0), (18, 0),      # 胸廓發育中，心胸比標準與成人不同
    (19, 1), (39, 1),       # 成人基線
    (40, 2), (59, 2),       # 吸菸/職業暴露累積
    (60, 3), (79, 3),       # COPD、心衰竭、肺炎高峰
    (80, 4), (95, 4),       # 高齡
])
def test_default_band_boundaries(age, band):
    assert b.age_band(age, DEFAULT_BOUNDS) == band


# ---------- age parsing ----------
@pytest.mark.parametrize("raw,years", [
    ("058Y", 58.0), ("58", 58.0), ("58.0", 58.0),   # 2017 vs 2020 release formats
    ("001Y", 1.0), ("0", 0.0),
])
def test_parse_age_years(raw, years):
    assert b.parse_age(raw) == pytest.approx(years)


def test_parse_age_month_and_day_units():
    assert b.parse_age("006M") == pytest.approx(0.5)
    assert b.parse_age("012D") == pytest.approx(12 / 365.0)
    # an infant must land in the 0-18 band, not be dropped
    assert b.age_band(b.parse_age("003M"), DEFAULT_BOUNDS) == 0


@pytest.mark.parametrize("raw", ["411", "413Y", "155", "148Y", "", None, "n/a", "-5"])
def test_parse_age_rejects_known_data_errors(raw):
    # Data_Entry_2017.csv carries transcription errors (148-155, 411-414). Unlike
    # PTB-XL's 300 these are NOT a >89 sentinel, so they must be dropped, not
    # folded into the top band.
    assert b.parse_age(raw) is None


# ---------- labels ----------
def test_parse_labels_splits_on_pipe():
    assert b.parse_labels("Cardiomegaly|Effusion") == {"Cardiomegaly", "Effusion"}
    assert b.parse_labels("No Finding") == {"No Finding"}
    assert b.parse_labels("") == set()


def test_every_label_maps_to_a_clinical_phrase():
    assert len(b.LABELS) == 15          # 14 findings + "No Finding"
    for label in b.LABELS:
        f = b.findings_for(label)
        assert f["findings"] and f["findings"][0]
        assert f["normality"] == ("normal" if label == b.NO_FINDING else "abnormal")


def test_findings_use_the_disambiguated_phrase_not_the_bare_nih_label():
    # "Effusion"/"Mass"/"Edema" alone are ambiguous (pericardial effusion, a
    # "massive" something, cerebral edema); the bank stores the chest phrase.
    assert b.findings_for("Effusion")["findings"] == ["pleural effusion"]
    assert b.findings_for("Mass")["findings"] == ["pulmonary mass"]
    assert b.findings_for("Edema")["findings"] == ["pulmonary edema"]


# ---------- bbox aliasing ----------
def test_bbox_label_alias_matches_data_entry_spelling():
    # BBox_List_2017.csv says "Infiltrate"; Data_Entry_2017.csv says "Infiltration".
    # Without the alias no infiltration film could ever be radiologist-verified.
    assert b.BBOX_LABEL_ALIASES["Infiltrate"] == "Infiltration"


# ---------- selection ----------
def test_selects_one_per_cell_and_counts_cells():
    recs = [
        rec(["Effusion"], sex="M", age=50, image="a.png", patient="1"),
        rec(["Effusion"], sex="F", age=25, image="b.png", patient="2"),
        rec(["Effusion"], sex="M", age=50, image="c.png", patient="3"),  # same cell as a
    ]
    sel, info = b.select_for_label(recs, "Effusion", 1, DEFAULT_BOUNDS)
    assert info["n_available"] == 3
    assert info["cells_available"] == 2
    assert info["n_selected"] == 2
    assert {s["rec"]["image"] for s in sel} == {"a.png", "b.png"}


def test_bbox_confirmed_film_wins_its_cell():
    # otherwise a cell could yield an unverifiable film while a radiologist-boxed
    # one sat right next to it, permanently hiding that cell from retrieval.
    recs = [
        rec(["Mass"], image="plain.png", patient="1", follow_up=0),
        rec(["Mass"], image="boxed.png", patient="2", follow_up=9),
    ]
    sel, info = b.select_for_label(recs, "Mass", 1, DEFAULT_BOUNDS,
                                   bbox_map={"boxed.png": {"Mass"}})
    assert [s["rec"]["image"] for s in sel] == ["boxed.png"]
    assert info["n_bbox"] == 1 and info["n_selected_bbox"] == 1


def test_pa_preferred_over_ap_and_fewer_co_findings_preferred():
    recs = [
        rec(["Nodule", "Effusion", "Mass"], image="busy.png", patient="1", view="PA"),
        rec(["Nodule"], image="ap.png", patient="2", view="AP"),
        rec(["Nodule"], image="clean.png", patient="3", view="PA"),
    ]
    sel, _ = b.select_for_label(recs, "Nodule", 1, DEFAULT_BOUNDS)
    assert [s["rec"]["image"] for s in sel] == ["clean.png"]


def test_cardiomegaly_rejects_ap_films():
    # An AP/portable film magnifies the cardiac silhouette, so the cardiothoracic
    # ratio is not assessable — such a film would teach a wrong reading.
    recs = [rec(["Cardiomegaly"], image="ap.png", view="AP", patient="1")]
    sel, info = b.select_for_label(recs, "Cardiomegaly", 1, DEFAULT_BOUNDS)
    assert info["n_available"] == 0 and sel == []
    recs.append(rec(["Cardiomegaly"], image="pa.png", view="PA", patient="2"))
    sel, info = b.select_for_label(recs, "Cardiomegaly", 1, DEFAULT_BOUNDS)
    assert [s["rec"]["image"] for s in sel] == ["pa.png"]


def test_normal_requires_no_finding_to_be_the_sole_label():
    recs = [
        rec(["No Finding"], image="clean.png", patient="1"),
        rec(["No Finding", "Nodule"], image="contradictory.png", patient="2"),
    ]
    sel, info = b.select_for_label(recs, b.NO_FINDING, 1, DEFAULT_BOUNDS)
    assert info["n_available"] == 1
    assert [s["rec"]["image"] for s in sel] == ["clean.png"]


def test_abnormal_row_that_also_says_no_finding_is_dropped():
    recs = [rec(["Nodule", "No Finding"], image="x.png", patient="1")]
    sel, info = b.select_for_label(recs, "Nodule", 1, DEFAULT_BOUNDS)
    assert info["n_available"] == 0 and sel == []


def test_one_film_per_patient_per_finding():
    # the same patient's follow-up series must not fill several cells
    recs = [
        rec(["Effusion"], sex="M", age=50, image="p1_a.png", patient="1", follow_up=0),
        rec(["Effusion"], sex="M", age=61, image="p1_b.png", patient="1", follow_up=1),
        rec(["Effusion"], sex="M", age=62, image="p2.png", patient="2", follow_up=0),
    ]
    sel, info = b.select_for_label(recs, "Effusion", 1, DEFAULT_BOUNDS)
    assert sorted(s["rec"]["patient"] for s in sel) == ["1", "2"]
    assert info["cells_eligible"] == 2
    assert info["cells_available"] == 2


def test_deduplication_never_reports_an_empty_cell_as_covered():
    # patient 1 owns the only film in both cells -> the second cell is eligible
    # but yields nothing, and must not be counted as covered.
    recs = [
        rec(["Effusion"], sex="M", age=50, image="a.png", patient="1", follow_up=0),
        rec(["Effusion"], sex="M", age=61, image="b.png", patient="1", follow_up=1),
    ]
    sel, info = b.select_for_label(recs, "Effusion", 1, DEFAULT_BOUNDS)
    assert info["cells_eligible"] == 2
    assert info["cells_available"] == 1 == len(sel)
    assert b.coverage_status(info) == "partial"


def test_missing_demographics_are_dropped():
    recs = [
        rec(["Nodule"], sex=None, image="nosex.png", patient="1"),
        rec(["Nodule"], age=None, image="noage.png", patient="2"),
    ]
    sel, info = b.select_for_label(recs, "Nodule", 1, DEFAULT_BOUNDS)
    assert info["n_available"] == 0 and sel == []


def test_per_cell_two_keeps_two_distinct_patients():
    recs = [rec(["Nodule"], image=f"{i}.png", patient=str(i)) for i in range(3)]
    sel, info = b.select_for_label(recs, "Nodule", 2, DEFAULT_BOUNDS)
    assert info["n_selected"] == 2


# ---------- coverage status ----------
def test_coverage_status():
    assert b.coverage_status({"n_available": 0, "cells_available": 0, "cells_total": 10}) == "none"
    assert b.coverage_status({"n_available": 5, "cells_available": 4, "cells_total": 10}) == "partial"
    assert b.coverage_status({"n_available": 40, "cells_available": 10, "cells_total": 10}) == "ok"


# ---------- manifest entries: the verification policy ----------
def sel_for(label="Mass", boxed=False, **kw):
    return {"rec": rec([label], **kw), "cell": ("M", 2), "bbox_verified": boxed}


def test_default_policy_verifies_only_radiologist_boxed_films():
    # ChestX-ray14 labels are NLP-mined at ~90% accuracy; only a film a
    # radiologist drew a box on may be shown without human review.
    assert b.make_entry("Mass", sel_for(boxed=True), "bbox")["verified"] is True
    assert b.make_entry("Mass", sel_for(boxed=False), "bbox")["verified"] is False


def test_verify_all_and_none_policies():
    assert b.make_entry("Mass", sel_for(boxed=False), "all")["verified"] is True
    assert b.make_entry("Mass", sel_for(boxed=True), "none")["verified"] is False


def test_entry_records_which_signal_verified_it():
    assert b.make_entry("Mass", sel_for(boxed=True), "bbox")["label_verification"] == "radiologist-bbox"
    assert b.make_entry("Mass", sel_for(boxed=False), "bbox")["label_verification"] == "nlp-mined"


def test_findings_exclude_unconfirmed_co_labels():
    # a film boxed for Mass must never be retrievable *because of* its Effusion
    # NLP co-label, which nobody confirmed.
    s = {"rec": rec(["Mass", "Effusion"], image="x.png"), "cell": ("M", 2),
         "bbox_verified": True}
    e = b.make_entry("Mass", s, "bbox")
    assert e["findings"] == ["pulmonary mass"]
    assert e["co_findings"] == ["Effusion"]


def test_entry_shape_matches_the_image_bank_contract():
    e = b.make_entry("Pneumothorax", sel_for("Pneumothorax", boxed=True, age=72.0, sex="F",
                                             image="00012345_007.png"), "bbox")
    assert e["id"] == "nih-pneumothorax-00012345_007"
    assert e["file"] == "cxr/nih-pneumothorax-00012345_007.png"
    assert e["modality"] == "CXR"
    assert e["normality"] == "abnormal"
    assert e["synthetic"] is False and e["deidentified"] is True
    assert e["age"] == 72 and e["sex"] == "F"
    assert e["source_url"] == b.SOURCE_URL


def test_normal_entry_is_marked_normal():
    e = b.make_entry(b.NO_FINDING, sel_for(b.NO_FINDING), "all")
    assert e["normality"] == "normal"
    assert e["id"].startswith("nih-no-finding-")


# ---------- CSV readers ----------
META_CSV = (
    "Image Index,Finding Labels,Follow-up #,Patient ID,Patient Age,Patient Gender,"
    "View Position,OriginalImage[Width,Height],OriginalImagePixelSpacing[x,y]\n"
    "00000001_000.png,Cardiomegaly,0,1,058Y,M,PA,2682,2749,0.143,0.143\n"
    "00000002_000.png,No Finding,0,2,031Y,F,PA,2894,2729,0.143,0.143\n"
    "00000003_000.png,Nodule|Mass,1,3,413Y,M,AP,2500,2048,0.168,0.168\n"
)
BBOX_CSV = ("Image Index,Finding Label,Bbox [x,y,w,h]\n"
            "00000001_000.png,Cardiomegaly,1,2,3,4\n"
            "00000003_000.png,Infiltrate,5,6,7,8\n")


def test_read_metadata_and_bbox(tmp_path):
    m = tmp_path / "Data_Entry_2017.csv"
    m.write_text(META_CSV, encoding="utf-8")
    recs = b.read_metadata(str(m))
    assert len(recs) == 3
    assert recs[0]["age"] == pytest.approx(58.0) and recs[0]["sex"] == "M"
    assert recs[1]["labels"] == {"No Finding"}
    assert recs[2]["age"] is None            # 413Y is a known data error

    bb = tmp_path / "BBox_List_2017.csv"
    bb.write_text(BBOX_CSV, encoding="utf-8")
    bmap = b.read_bbox(str(bb))
    assert bmap["00000001_000.png"] == {"Cardiomegaly"}
    assert bmap["00000003_000.png"] == {"Infiltration"}   # alias applied
    assert b.read_bbox(str(tmp_path / "missing.csv")) == {}


# The corrected 2020 re-release renames "Patient Gender" -> "Patient Sex" and
# writes ages as plain integers. Reading only the 2017 spelling makes every
# record demographically incomplete, which surfaces as a fully-zero coverage
# report rather than an error -- so both spellings are pinned here.
META_CSV_V2020 = (
    "Image Index,Finding Labels,Follow-up #,Patient ID,Patient Age,Patient Sex,"
    "View Position,OriginalImage[Width,Height],OriginalImagePixelSpacing[x,y]\n"
    "00000001_000.png,Cardiomegaly,0,1,57,M,PA,2682,2749,0.143,0.143\n"
    "00000002_000.png,No Finding,0,2,80,F,PA,2500,2048,0.171,0.171\n"
)


def test_read_metadata_accepts_the_v2020_column_names(tmp_path):
    m = tmp_path / "Data_Entry_2017_v2020.csv"
    m.write_text(META_CSV_V2020, encoding="utf-8")
    recs = b.read_metadata(str(m))
    assert [r["sex"] for r in recs] == ["M", "F"]
    assert [r["age"] for r in recs] == [pytest.approx(57.0), pytest.approx(80.0)]
    assert recs[0]["view"] == "PA" and recs[0]["patient"] == "1"


def test_read_metadata_rejects_a_file_with_no_sex_column(tmp_path):
    m = tmp_path / "broken.csv"
    m.write_text(META_CSV_V2020.replace("Patient Sex", "Sex"), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        b.read_metadata(str(m))
    assert "Patient Sex or Patient Gender" in str(exc.value)


def test_build_index_buckets_every_label(tmp_path):
    m = tmp_path / "meta.csv"
    m.write_text(META_CSV, encoding="utf-8")
    idx = b.build_index(b.read_metadata(str(m)))
    assert len(idx["Cardiomegaly"]) == 1
    assert len(idx["Nodule"]) == 1 and len(idx["Mass"]) == 1
    assert len(idx[b.NO_FINDING]) == 1


def test_end_to_end_list_only_report(tmp_path):
    m = tmp_path / "meta.csv"
    m.write_text(META_CSV, encoding="utf-8")
    idx = b.build_index(b.read_metadata(str(m)))
    rows = []
    for label in b.LABELS:
        _sel, info = b.select_for_label(idx.get(label, []), label, 1, DEFAULT_BOUNDS)
        rows.append({"label": label, "info": info, "status": b.coverage_status(info)})
    out = tmp_path / "cov.md"
    b.write_coverage_report(str(out), rows, DEFAULT_BOUNDS, "bbox")
    text = out.read_text(encoding="utf-8")
    assert "NIH ChestX-ray14" in text
    assert "`Cardiomegaly`" in text and "心臟肥大" in text
    # 3-row fixture: only Cardiomegaly + No Finding are usable (the third row's
    # age is a data error), so 13 findings must report `none`.
    assert text.count("| none |") == 13
