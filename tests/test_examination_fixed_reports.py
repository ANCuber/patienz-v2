"""The fixed-report lookup in page/examination.py (§6-A image-first).

``examination.csv`` spells the same study both 「胸部X光」 and 「胸部 X 光」. If the
lookup were an exact compare, ordering the spaced variant would silently fall
back to the examiner model — losing the determinism the paired image depends on.

The page runs Streamlit at import, so the two helpers are extracted from the
real source text with ``ast`` and executed in isolation: the test still fails if
the shipped code changes, without needing a Streamlit runtime.
"""
import ast
import io
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "page", "examination.py")
WANTED = ("_norm_exam_key", "_fixed_report_for")


@pytest.fixture(scope="module")
def ex():
    """Namespace holding just the two lookup helpers, as written in the page."""
    tree = ast.parse(io.open(PAGE, encoding="utf-8").read(), filename=PAGE)
    picked = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {n.name for n in picked} == set(WANTED), "helpers renamed or removed"
    ns = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), PAGE, "exec"), ns)
    return ns


FIXED = {"心電圖": "**[心電圖] 檢查結果：** …\n[ABNORMAL]",
         "胸部X光": "**[胸部X光] 檢查結果：** …\n[ABNORMAL]"}


def test_exam_key_folding_ignores_spacing_and_case(ex):
    fold = ex["_norm_exam_key"]
    assert fold("胸部 X 光") == fold("胸部X光")
    assert fold("Chest X-ray") == fold("chest x-ray")
    assert fold(None) == ""


def test_both_spellings_of_the_chest_film_get_the_fixed_report(ex):
    for_item = ex["_fixed_report_for"]
    assert for_item({"chinese": "胸部 X 光", "eng": "Chest X-ray"}, FIXED) == FIXED["胸部X光"]
    assert for_item({"chinese": "胸部X光", "eng": "Chest X-ray"}, FIXED) == FIXED["胸部X光"]
    # an exam the case did not fix still falls through to the model path
    assert for_item({"chinese": "腹部X光", "eng": "Abdominal X-ray"}, FIXED) is None


def test_the_english_name_also_matches_a_report_keyed_in_english(ex):
    fixed = {"Electrocardiography": "**[心電圖] 檢查結果：** …\n[NORMAL]"}
    got = ex["_fixed_report_for"]({"chinese": "心電圖", "eng": "Electrocardiography"}, fixed)
    assert got == fixed["Electrocardiography"]
