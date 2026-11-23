"""The grading prompts must not see the case's private bookkeeping.

``_template_meta`` records which film a template case was written against and
the exact report text expected for it; ``_image_binding`` logs the binding run.
Handing either to the mark-scheme setter or the grader would show the model the
answer key. page/config.py already strips them for the patient/examiner prompts
(``case_imaging.prompt_json``); this pins that page/grade.py does the same.

The page runs Streamlit at import, so the check is made on the source text.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADE = os.path.join(ROOT, "page", "grade.py")


def test_grade_builds_patient_setup_through_prompt_json():
    src = io.open(GRADE, encoding="utf-8").read()
    setup_lines = [ln.strip() for ln in src.splitlines()
                   if "虛擬病人設定" in ln and "ss.data" in ln]
    assert setup_lines, "the patient-setup prompt block disappeared"
    for ln in setup_lines:
        assert "case_imaging.prompt_json(ss.data)" in ln, ln
        assert "json.dumps(ss.data" not in ln, ln
    assert re.search(r"^import util\.case_imaging as case_imaging$", src, re.M)


def test_no_raw_case_dump_anywhere_in_grade():
    src = io.open(GRADE, encoding="utf-8").read()
    assert "json.dumps(ss.data" not in src
