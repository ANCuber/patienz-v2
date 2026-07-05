"""One-click test panel expansion (proposal §3-A)."""
from util.exam_panels import expand_panels

# Mirrors examination_choice.json + examination.csv indexing:
# json l/r are 1-based and exclude the subcategory header row; the page slices
# csv_rows[l-1:r-1].
EXAM_CHOICE = {
    "實驗室檢查": {"血液檢驗": {"l": 3, "r": 5}},
    "影像檢查": {"X光": {"l": 6, "r": 8}},
}
CSV_ROWS = [
    ["檢驗項目", "名稱", "參考值", "單位"],   # 0 header
    ["血液檢驗", "", "", ""],                  # 1 subcat header (excluded by range)
    ["Hb", "血紅素", "13-17", "g/dL"],         # 2  (l=3)
    ["WBC", "白血球", "4-10", "10^3/uL"],      # 3      r=5 -> rows[2:4]
    ["X光", "", "", ""],                       # 4 subcat header
    ["CXR", "胸部X光", "", ""],                # 5  (l=6)
    ["KUB", "腹部X光", "", ""],                # 6      r=8 -> rows[5:7]
]
TEXT_TYPES = {"X光", "CT", "MRI"}


def test_expand_collects_items_across_subcategories():
    entries = expand_panels(["血液檢驗", "X光"], EXAM_CHOICE, CSV_ROWS, TEXT_TYPES)
    engs = {e["eng"] for e in entries}
    assert engs == {"Hb", "WBC", "CXR", "KUB"}


def test_result_type_marks_text_vs_value():
    entries = {e["eng"]: e for e in expand_panels(["血液檢驗", "X光"], EXAM_CHOICE, CSV_ROWS, TEXT_TYPES)}
    assert entries["Hb"]["result_type"] == "value"
    assert entries["CXR"]["result_type"] == "text"
    assert entries["Hb"]["chinese"] == "血紅素"


def test_dedup_and_unknown_subcat_ignored():
    entries = expand_panels(["血液檢驗", "血液檢驗", "不存在的子類別"], EXAM_CHOICE, CSV_ROWS, TEXT_TYPES)
    assert sorted(e["eng"] for e in entries) == ["Hb", "WBC"]


def test_empty_input():
    assert expand_panels([], EXAM_CHOICE, CSV_ROWS, TEXT_TYPES) == []
