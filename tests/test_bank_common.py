"""Shared plumbing behind both §6-A dataset builders (tools/bank_common.py).

The banding/sex/coverage helpers are re-exported by build_ptbxl_bank and
build_nih_cxr_bank, so their own suites cover the ECG/CXR-specific behaviour;
what is pinned here is the part neither of those exercises — the report format
and the atomic manifest I/O that a half-finished build must not be able to
corrupt.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import bank_common as bc  # noqa: E402


# ---------- bands ----------
def test_parse_bands_normalizes():
    assert bc.parse_bands("0,19,40,60,80") == [0, 19, 40, 60, 80]
    assert bc.parse_bands("80,40,19,60") == [0, 19, 40, 60, 80]   # zero implied, sorted
    assert bc.parse_bands("19,19,40") == [0, 19, 40]              # duplicates forgiven
    assert bc.parse_bands("-5,40") == [0, 40]                     # negatives dropped


def test_band_labels():
    assert bc.band_labels([0, 19, 40, 60, 80]) == ["0-18", "19-39", "40-59", "60-79", "80+"]
    assert bc.band_labels([0, 19, 36, 51, 66]) == ["0-18", "19-35", "36-50", "51-65", "66+"]


def test_age_above_top_bound_folds_into_the_open_ended_band():
    # PTB-XL de-identifies >89 as 300; it belongs in the top band, not its own.
    assert bc.age_band(300, [0, 19, 36, 51, 66]) == 4


def test_age_band_rejects_unusable_values():
    assert bc.age_band(None, [0, 40]) is None
    assert bc.age_band("", [0, 40]) is None
    assert bc.age_band("n/a", [0, 40]) is None


@pytest.mark.parametrize("raw,mf", [
    ("0", "M"), ("1", "F"),            # PTB-XL numeric convention
    ("M", "M"), ("F", "F"),            # NIH ChestX-ray14 convention
    ("male", "M"), ("Female", "F"), ("男", "M"), ("女", "F"),
    ("2", None), ("", None), (None, None),
])
def test_norm_sex_covers_both_dataset_conventions(raw, mf):
    assert bc.norm_sex(raw) == mf


def test_cells_total():
    assert bc.cells_total([0, 19, 40, 60, 80]) == 10   # 2 sexes x 5 bands


# ---------- coverage ----------
def test_coverage_status():
    assert bc.coverage_status({"n_available": 0, "cells_available": 0, "cells_total": 10}) == "none"
    assert bc.coverage_status({"n_available": 3, "cells_available": 2, "cells_total": 10}) == "partial"
    assert bc.coverage_status({"n_available": 30, "cells_available": 10, "cells_total": 10}) == "ok"
    assert bc.coverage_status({}) == "none"


def test_summarize():
    rows = [
        {"status": "ok", "info": {"n_selected": 10}},
        {"status": "partial", "info": {"n_selected": 4}},
        {"status": "none", "info": {"n_selected": 0}},
    ]
    s = bc.summarize(rows)
    assert (s["n_classes"], s["n_any"], s["n_ok"], s["n_partial"], s["n_none"]) == (3, 2, 1, 1, 1)
    assert s["n_images"] == 14 and s["cells_filled"] == 14


# ---------- report ----------
def test_write_report_puts_the_gaps_first(tmp_path):
    out = tmp_path / "cov.md"
    bc.write_report(str(out), "T", ["one", "two"], ["a", "b"], [
        ("ok", 0, ["good", 1]),
        ("none", 0, ["missing", 2]),
        ("partial", 0, ["some", 3]),
    ], ["note"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# T"
    assert lines[1:4] == ["", "- one", "- two"]
    body = [ln for ln in lines if ln.startswith("| ") and "---" not in ln]
    # honest gaps at the top, successes at the bottom
    assert [b.split("|")[1].strip() for b in body] == ["a", "missing", "some", "good"]
    assert lines[-1] == "> note"


def test_write_report_creates_missing_directories(tmp_path):
    out = tmp_path / "nested" / "deeper" / "cov.md"
    bc.write_report(str(out), "T", [], ["a"], [("ok", 0, ["x"])], [])
    assert out.is_file()


# ---------- manifest I/O ----------
def test_manifest_roundtrip_and_atomicity(tmp_path):
    d = str(tmp_path)
    bc.save_manifest(d, {"schema_version": 1, "entries": [{"id": "e1", "caption": "肺炎"}]})
    assert bc.load_manifest(d)["entries"][0]["caption"] == "肺炎"   # utf-8, not escaped
    assert "肺炎" in (tmp_path / "manifest.json").read_text(encoding="utf-8")
    # the temp file must not survive the write
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_load_manifest_tolerates_missing_or_broken_files(tmp_path):
    d = str(tmp_path)
    assert bc.load_manifest(d) == {"schema_version": 1, "entries": []}
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    assert bc.load_manifest(d)["entries"] == []
    (tmp_path / "manifest.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert bc.load_manifest(d)["entries"] == []
    (tmp_path / "manifest.json").write_text(json.dumps({"entries": "nope"}), encoding="utf-8")
    assert bc.load_manifest(d)["entries"] == []


def test_save_manifest_replaces_rather_than_appends(tmp_path):
    d = str(tmp_path)
    bc.save_manifest(d, {"schema_version": 1, "entries": [{"id": "a"}]})
    bc.save_manifest(d, {"schema_version": 1, "entries": [{"id": "a"}, {"id": "b"}]})
    assert [e["id"] for e in bc.load_manifest(d)["entries"]] == ["a", "b"]
    assert len(os.listdir(d)) == 1
