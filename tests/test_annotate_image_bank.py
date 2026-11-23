"""The clinician review round trip (tools/annotate_image_bank.py).

Two manifest fields need a human: ``verified`` (NIH's NLP-mined labels are ~90%
accurate) and ``attributes.laterality`` (no open chest dataset records the side).
This tool is how those get set, so what is pinned here is that a review cannot
be silently lost, mis-parsed, or applied to the wrong entry.
"""
import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import annotate_image_bank as an  # noqa: E402


def _bank(tmp_path, entries):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8")
    return str(tmp_path)


def _entry(ident, finding, *, verified=True, laterality=None, legacy=False, reviewed=True):
    # ``reviewed`` is the local clinician sign-off. It defaults on so tests about
    # laterality/verified are not also asserting label provenance; the tests that
    # are about provenance pass reviewed=False.
    e = {"id": ident, "file": "cxr/%s.png" % ident, "modality": "CXR",
         "normality": "abnormal", "findings": [finding], "verified": verified}
    if reviewed:
        e["clinician_reviewed"] = True
    if not legacy:
        e["umbrella_terms"] = []
        e["attributes"] = {"territory": [], "completeness": None,
                           "lesion": None, "laterality": laterality}
    return e


def _read(directory):
    with open(os.path.join(directory, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)["entries"]


# ---------- worklist ----------
def test_needs_review_flags_unverified_and_unannotated_sides():
    assert an.needs_review(_entry("a", "pneumothorax", verified=False)) == "unverified"
    assert an.needs_review(_entry("b", "pneumothorax")) == "laterality-unknown"
    assert an.needs_review(_entry("c", "pneumothorax", laterality="right")) is None
    # cardiomegaly has no side to get wrong, so it is finished once verified
    assert an.needs_review(_entry("d", "cardiomegaly")) is None


def test_export_puts_the_blocking_rows_first(tmp_path):
    entries = [_entry("done", "cardiomegaly"),
               _entry("side", "pleural effusion"),
               _entry("hidden", "pneumonia", verified=False, reviewed=False)]
    out = tmp_path / "review.csv"
    n = an.export_csv(entries, str(out), include_done=False)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert n == 2
    assert [r["id"] for r in rows] == ["hidden", "side"]
    assert [r["note"] for r in rows] == ["unverified", "laterality-unknown"]


def test_export_can_include_finished_entries(tmp_path):
    out = tmp_path / "all.csv"
    assert an.export_csv([_entry("done", "cardiomegaly")], str(out), include_done=True) == 1


# ---------- applying decisions ----------
def test_round_trip_sets_verified_and_laterality(tmp_path):
    entries = [_entry("e1", "pneumothorax", verified=False)]
    out = tmp_path / "review.csv"
    an.export_csv(entries, str(out), include_done=False)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    rows[0]["verified"] = "true"
    rows[0]["laterality"] = "right"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=an.FIELDS)
        w.writeheader()
        w.writerows(rows)

    seen, updated, problems = an.apply_csv(entries, str(out))
    assert (seen, updated, problems) == (1, 1, [])
    assert entries[0]["verified"] is True
    assert entries[0]["attributes"]["laterality"] == "right"
    assert entries[0]["attributes"]["laterality_reviewed"] is True


def test_none_records_a_decision_that_the_finding_has_no_side():
    e = _entry("e", "cardiomegaly")
    an.apply_row(e, {"laterality": "none"}, [])
    assert e["attributes"]["laterality"] is None
    assert e["attributes"]["laterality_reviewed"] is True   # reviewed, not skipped


def test_blank_cells_change_nothing():
    e = _entry("e", "pneumothorax", laterality="left")
    assert an.apply_row(e, {"verified": "", "laterality": "", "note": ""}, []) is False
    assert e["attributes"]["laterality"] == "left"


@pytest.mark.parametrize("row,bad", [
    ({"laterality": "righ"}, "bad laterality"),
    ({"verified": "maybe"}, "bad verified"),
])
def test_bad_values_are_reported_not_silently_applied(row, bad):
    e = _entry("e", "pneumothorax", laterality="left")
    problems = []
    an.apply_row(e, row, problems)
    assert problems and bad in problems[0]
    assert e["attributes"]["laterality"] == "left"       # untouched
    assert e["verified"] is True


def test_unknown_ids_are_reported(tmp_path):
    out = tmp_path / "r.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=an.FIELDS)
        w.writeheader()
        w.writerow({"id": "ghost", "verified": "true"})
    seen, updated, problems = an.apply_csv([_entry("real", "pneumothorax")], str(out))
    assert (seen, updated) == (1, 0)
    assert "unknown id: ghost" in problems[0]


def test_a_reviewer_note_is_kept():
    e = _entry("e", "pneumothorax")
    an.apply_row(e, {"note": "膜線不清，建議換片"}, [])
    assert e["review_note"] == "膜線不清，建議換片"


# ---------- legacy upgrade ----------
def test_backfill_splits_findings_and_computes_attributes():
    legacy = {"id": "old", "file": "ecg/old.png", "modality": "ECG", "verified": True,
              "normality": "abnormal",
              "findings": ["inferior myocardial infarction", "myocardial infarction"]}
    an.backfill([legacy])
    assert legacy["findings"] == ["inferior myocardial infarction"]
    assert legacy["umbrella_terms"] == ["myocardial infarction"]
    assert legacy["attributes"]["territory"] == ["inferior"]
    assert legacy["attributes"]["lesion"] == "infarction"


# ---------- publishing without a review ----------
def test_publish_shows_the_films_but_claims_no_review():
    hidden = _entry("h", "pneumonia", verified=False, reviewed=False)
    hidden["label_verification"] = "nlp-mined"
    other = {"id": "ecg", "modality": "ECG", "file": "ecg/e.png", "verified": False,
             "findings": ["atrial fibrillation"], "normality": "abnormal"}
    assert an.publish([hidden, other], ["CXR"]) == 1
    assert hidden["verified"] is True
    assert hidden["published_without_review"] is True
    assert "clinician_reviewed" not in hidden      # nobody looked at it
    assert other["verified"] is False              # a different modality is untouched


def test_a_published_but_unchecked_film_stays_on_the_worklist():
    e = _entry("e", "pneumonia", reviewed=False)
    e["label_verification"] = "nlp-mined"
    assert an.needs_review(e) == "label-unreviewed"
    e["label_verification"] = "radiologist-bbox"
    assert an.needs_review(e) == "label-unreviewed", "a bounding box is not a label review"
    e["clinician_reviewed"] = True
    assert an.needs_review(e) == "laterality-unknown"   # next outstanding item


def test_setting_verified_in_the_csv_counts_as_the_local_review():
    e = _entry("e", "pneumonia", verified=False, reviewed=False)
    an.apply_row(e, {"verified": "true"}, [])
    assert e["verified"] is True and e["clinician_reviewed"] is True


def test_clearing_verified_records_the_rejection_without_claiming_a_vouch():
    """A rejection must be stored, but must never read as "a clinician vouches".

    Before this was recorded, ``verified: false`` looked exactly like "nobody has
    looked yet", and ``--publish`` flipped the film back on for students.
    """
    e = _entry("e", "pneumonia", reviewed=False)
    an.apply_row(e, {"verified": "false"}, [])
    assert e["verified"] is False
    assert "clinician_reviewed" not in e          # not vouched for
    assert e["review_verdict"] == "rejected"      # but decided
    assert an.needs_review(e) is None             # so it leaves the worklist
    assert an.publish([e], ["CXR"]) == 0 and e["verified"] is False
    # accepting it later both vouches and clears the published-unreviewed mark
    e["published_without_review"] = True
    an.apply_row(e, {"verified": "true"}, [])
    assert e["clinician_reviewed"] is True and e["review_verdict"] == "accepted"
    assert "published_without_review" not in e


def test_backfill_records_the_ptbxl_label_provenance():
    e = {"id": "p", "modality": "ECG", "file": "ecg/p.png", "verified": True,
         "normality": "abnormal", "findings": ["atrial fibrillation"],
         "source": "PTB-XL (PhysioNet)"}
    an.backfill([e])
    assert e["label_verification"] == "cardiologist-validated"


def test_recompute_refreshes_derived_values_but_keeps_the_reviewers():
    # the gate prefers stored attributes over derived ones, so a vocabulary fix
    # only reaches an existing bank through this path
    e = {"id": "e", "file": "ecg/e.png", "modality": "ECG", "normality": "abnormal",
         "verified": True, "findings": ["left anterior fascicular block"],
         "attributes": {"territory": ["anterior"], "completeness": None, "lesion": None,
                        "laterality": "right", "laterality_reviewed": True}}
    assert an.recompute_attributes([e]) == 1
    assert e["attributes"]["territory"] == []            # re-derived by the new vocabulary
    assert e["attributes"]["laterality"] == "right"      # a human's decision, preserved
    assert e["attributes"]["laterality_reviewed"] is True
    assert an.recompute_attributes([e]) == 0             # idempotent


def test_backfill_does_not_disturb_an_already_upgraded_entry():
    e = _entry("new", "pneumothorax", laterality="right")
    before = json.dumps(e, sort_keys=True)
    an.backfill([e])
    assert json.dumps(e, sort_keys=True) == before


def test_main_writes_only_when_something_changed(tmp_path, capsys):
    d = _bank(tmp_path, [{"id": "old", "file": "ecg/old.png", "modality": "ECG",
                          "normality": "abnormal", "verified": True,
                          "findings": ["atrial fibrillation"]}])
    assert an.main.__module__            # sanity: module imported, not a stub
    sys.argv = ["annotate", "--bank-dir", d, "--backfill"]
    assert an.main() == 0
    assert "attributes" in _read(d)[0]
    sys.argv = ["annotate", "--bank-dir", d, "--backfill", "--dry-run"]
    assert an.main() == 0
    out = capsys.readouterr().out
    assert "backfilled 0" in out
