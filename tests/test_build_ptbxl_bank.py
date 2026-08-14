"""Pure selection/coverage logic of tools/build_ptbxl_bank.py (§6-A).

Rendering (wfdb/matplotlib) is not exercised here; these tests pin the record
selection, demographic bucketing, likelihood/validation gating, and coverage
accounting that decide *which* PTB-XL records become bank images.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_ptbxl_bank as b  # noqa: E402


DX_IMI = {"category": "diagnostic", "normality": "abnormal", "subclass": "IMI",
          "description": "inferior myocardial infarction"}
RHY_AFIB = {"category": "rhythm", "normality": "abnormal", "subclass": "",
            "description": "atrial fibrillation"}
NORM = {"category": "diagnostic", "normality": "normal", "subclass": "NORM",
        "description": "normal ECG"}


def rec(parsed, human=True, sex="M", age=50, ecg_id=1, fold=10, noise=0.0):
    return {"parsed": parsed, "human": human, "sex": sex, "age": age, "fold": fold,
            "noise": noise, "ecg_id": str(ecg_id), "ecg_id_int": ecg_id, "row": None}


# ---------- bands / bins / helpers ----------
def test_parse_bands_prepends_zero_and_sorts():
    assert b.parse_bands("0,40,60") == [0, 40, 60]
    assert b.parse_bands("40,60") == [0, 40, 60]
    assert b.parse_bands("60,40") == [0, 40, 60]


@pytest.mark.parametrize("age,band", [(30, 0), (39, 0), (40, 1), (59, 1), (60, 2), (95, 2)])
def test_age_band(age, band):
    assert b.age_band(age, [0, 40, 60]) == band


def test_age_band_missing():
    assert b.age_band(None, [0, 40, 60]) is None
    assert b.age_band("n/a", [0, 40, 60]) is None


@pytest.mark.parametrize("v,mf", [("0", "M"), ("1", "F"), ("M", "M"), ("female", "F"),
                                  ("2", None), ("", None)])
def test_sex_mf(v, mf):
    assert b._sex_mf(v) == mf


def test_is_clean():
    assert b._is_clean({"NORM": 100.0, "SR": 0.0}) is True
    assert b._is_clean({"NORM": 100, "IMI": 80}) is False
    assert b._is_clean({"SR": 0, "PVC": 100}) is False


# ---------- select_for_code ----------
def test_diagnostic_threshold_validation_and_cells():
    recs = [
        rec({"IMI": 90}, sex="M", age=50, ecg_id=1),   # cell (M,1) keep
        rec({"IMI": 70}, sex="M", age=50, ecg_id=2),   # below 80 -> drop
        rec({"IMI": 85}, sex="F", age=30, ecg_id=3),   # cell (F,0) keep
        rec({"IMI": 95}, sex="M", age=50, ecg_id=4, human=False),  # not human -> drop
        rec({"IMI": 88}, sex="M", age=50, ecg_id=5),   # same cell (M,1) as #1
    ]
    sel, info = b.select_for_code(recs, "IMI", DX_IMI, per_cell=1, bounds=[0, 40, 60],
                                  min_likelihood=80)
    assert info["n_available"] == 3          # #1,#3,#5
    assert info["cells_available"] == 2
    assert info["n_selected"] == 2
    assert {s["rec"]["ecg_id"] for s in sel} == {"1", "3"}  # best per cell (90>88)


def test_per_cell_two_keeps_both_in_cell():
    recs = [rec({"IMI": 90}, ecg_id=1), rec({"IMI": 88}, ecg_id=5)]  # same cell (M,1)
    sel, info = b.select_for_code(recs, "IMI", DX_IMI, per_cell=2, bounds=[0, 40, 60],
                                  min_likelihood=80)
    assert info["n_selected"] == 2


def test_rhythm_accepts_zero_likelihood():
    # form/rhythm statements are recorded likelihood 0; presence must suffice
    recs = [rec({"AFIB": 0.0}, sex="F", age=70, ecg_id=9)]
    sel, info = b.select_for_code(recs, "AFIB", RHY_AFIB, per_cell=1, bounds=[0, 40, 60],
                                  min_likelihood=80)
    assert info["n_selected"] == 1


def test_norm_requires_clean_tracing():
    recs = [
        rec({"NORM": 100.0, "SR": 0.0}, ecg_id=1),   # clean -> keep
        rec({"NORM": 100.0, "IMI": 80.0}, ecg_id=2),  # co-pathology -> drop
    ]
    sel, info = b.select_for_code(recs, "NORM", NORM, per_cell=1, bounds=[0, 40, 60],
                                  min_likelihood=80)
    assert info["n_selected"] == 1
    assert sel[0]["rec"]["ecg_id"] == "1"


# ---------- coverage status ----------
def test_coverage_status():
    assert b.coverage_status({"n_available": 0, "cells_available": 0, "cells_total": 6}) == "none"
    assert b.coverage_status({"n_available": 5, "cells_available": 3, "cells_total": 6}) == "partial"
    assert b.coverage_status({"n_available": 9, "cells_available": 6, "cells_total": 6}) == "ok"


# ---------- findings / umbrella mapping ----------
def test_findings_umbrella_terms():
    imi = b.findings_for("IMI", DX_IMI)
    assert imi["normality"] == "abnormal"
    assert "myocardial infarction" in imi["findings"]      # umbrella so 心肌梗塞 matches
    assert b.findings_for("NORM", NORM)["normality"] == "normal"
    assert b.umbrella_terms("AFIB", "", "atrial fibrillation") == ["atrial fibrillation"]
    assert "left bundle branch block" in b.umbrella_terms("CLBBB", "CLBBB",
                                                          "complete left bundle branch block")
