"""通用 ACGME milestone PDF → JSON 萃取器。

用法：
    python tools/extract_milestone_pdf.py <pdf_path> <output_json_path> [--specialty <name>]

輸出格式與 config/acgme_milestones/internal_medicine.json 一致。
levels 文字保留英文原文（ACGME 官方），name_zh 由 ZH_NAMES 表提供（缺則為英文）。
"""
import argparse
import json
import os
import re
import sys

import pdfplumber


DOMAIN_MAP = {
    "Patient Care": "PC",
    "Medical Knowledge": "MK",
    "Systems-Based Practice": "SBP",
    "Practice-Based Learning and Improvement": "PBLI",
    "Professionalism": "PROF",
    "Interpersonal and Communication Skills": "ICS",
}

# 標題模式：捕獲 (domain_full, number, name)
TITLE_RE = re.compile(
    r"^\s*(Patient Care|Medical Knowledge|Systems-Based Practice|"
    r"Practice-Based Learning and Improvement|Professionalism|"
    r"Interpersonal and Communication Skills)\s+(\d+):\s*(.+?)\s*$"
)

# 中文翻譯對照表（依 ACGME 2.0 常見子能力命名）
ZH_NAMES = {
    "History": "病史採集",
    "Physical Examination": "理學檢查",
    "Clinical Reasoning": "臨床推理",
    "Patient Management": "病人管理",
    "Patient Management – Inpatient": "住院病人管理",
    "Patient Management – Outpatient": "門診病人管理",
    "Digital Health": "數位健康",
    "Applied Foundational Sciences": "應用基礎科學",
    "Therapeutic Knowledge": "治療知識",
    "Knowledge of Diagnostic Testing": "診斷檢驗知識",
    "Patient Safety and Quality Improvement": "病人安全與品質改善",
    "System Navigation for Patient-Centered Care": "以病人為中心的系統導航",
    "Physician Role in Health Care Systems": "醫師於醫療體系中的角色",
    "Evidence-Based and Informed Practice": "實證導向實踐",
    "Reflective Practice and Commitment to Personal Growth": "反思實踐與個人成長承諾",
    "Professional Behavior": "專業行為",
    "Ethical Principles": "倫理原則",
    "Accountability/Conscientiousness": "責任感與謹慎態度",
    "Self-Awareness and Help-Seeking": "自我覺察與求助行為",
    "Knowledge of Systemic and Individual Factors of Well-Being": "個人與系統層面身心健康因素之認識",
    "Patient- and Family-Centered Communication": "以病人與家屬為中心之溝通",
    "Interprofessional and Team Communication": "跨專業與團隊溝通",
    "Communication within Health Care Systems": "於醫療體系內之溝通",
    "Procedures": "程序操作",
    "Catheterization": "心導管",
    "Echocardiography": "心臟超音波",
    "Electrocardiography": "心電圖",
    "Stress Testing": "壓力測試",
}


def normalize_text(s: str) -> str:
    if not s:
        return ""
    # 連字號斷行還原（hypothesis-\ndriven → hypothesis-driven）
    s = re.sub(r"-\n", "-", s)
    # 視覺換行轉空格（cell 內 \n 多為 column wrap，非條目分隔）
    s = s.replace("\n", " ")
    # 多重空白收縮
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _find_title(table) -> tuple | None:
    """掃 row 0 找符合 TITLE_RE 的 cell；回傳 (column_index, match) 或 None。"""
    if not table or not table[0]:
        return None
    for ci, cell in enumerate(table[0]):
        if not cell:
            continue
        m = TITLE_RE.match(cell.strip())
        if m:
            return ci, m
    return None


def _find_level_header_row(table) -> int | None:
    """找含 "Level 1".."Level 5" 的 row index；至少要有 4 個 Level 字樣。"""
    for ri, row in enumerate(table):
        if not row:
            continue
        hits = sum(1 for c in row if c and "Level" in c and any(d in c for d in "12345"))
        if hits >= 4:
            return ri
    return None


def _extract_levels_from_header_row(table, header_row_idx: int) -> dict | None:
    """從 header row 之後第一個有 5 個 cell 內容的 row，依 header 欄位對應 Level 1-5。"""
    header = table[header_row_idx]
    # 建立 column_index -> level number 的對映
    col_to_lvl = {}
    for ci, cell in enumerate(header):
        if not cell:
            continue
        for d in "12345":
            if f"Level {d}" in cell:
                col_to_lvl[ci] = d
                break
    if len(col_to_lvl) < 5:
        return None

    # 找 body row：取 header 之後、有足夠內容的 row
    for ri in range(header_row_idx + 1, len(table)):
        row = table[ri]
        if not row:
            continue
        filled = sum(1 for ci in col_to_lvl if ci < len(row) and row[ci])
        if filled >= 5:
            levels = {}
            for ci, lvl in col_to_lvl.items():
                levels[lvl] = normalize_text(row[ci] or "")
            if all(levels.get(str(i)) for i in range(1, 6)):
                return levels
    return None


def extract_subcompetency_from_table(table) -> dict | None:
    """從一個 pdfplumber table 抽出子能力資訊；不符合格式則回傳 None。

    支援兩種格式：
    - Format A（internal medicine 等）：title 在 col 0，header 在 row 1，levels 在 row 2 的 5 個 col
    - Format B（psychiatry 等）：title 在某個 col；sub-items 占多行；Level header 在較後的 row，
      levels 散布於非連續 col（如 col 0,2,3,4,5）
    """
    if not table or len(table) < 3:
        return None
    found = _find_title(table)
    if not found:
        return None
    _, m = found
    domain_full, number, name = m.group(1), int(m.group(2)), m.group(3).strip()
    domain_short = DOMAIN_MAP[domain_full]

    header_idx = _find_level_header_row(table)
    if header_idx is None:
        return None

    levels = _extract_levels_from_header_row(table, header_idx)
    if levels is None:
        return None

    clean_name = name.rstrip("*").strip()
    name_zh = ZH_NAMES.get(clean_name, clean_name)

    return {
        "id": f"{domain_short}{number}",
        "domain": domain_short,
        "name_en": clean_name,
        "name_zh": name_zh,
        "levels": levels,
    }


def extract_milestones(pdf_path: str, specialty_label: str | None = None) -> dict:
    subs = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                rec = extract_subcompetency_from_table(table)
                if rec:
                    subs.append(rec)

    return {
        "milestone_source": os.path.basename(pdf_path),
        "specialty": specialty_label or os.path.basename(pdf_path).replace("milestones.pdf", ""),
        "version": "ACGME Milestones (auto-extracted from PDF)",
        "language_levels": "en",
        "language_names_zh": "zh-TW",
        "disclaimer": (
            "本檔由 PDF 自動萃取，levels 為 ACGME 英文原文；name_zh 由內建翻譯表提供，"
            "未匹配項目以英文呈現，可後續人工校對補上中譯。"
        ),
        "subcompetencies": subs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("output")
    ap.add_argument("--specialty", default=None)
    args = ap.parse_args()

    data = extract_milestones(args.pdf, args.specialty)
    if not data["subcompetencies"]:
        print(f"[WARN] 未從 {args.pdf} 萃取出任何子能力", file=sys.stderr)
        sys.exit(2)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    n = len(data["subcompetencies"])
    from collections import Counter
    dist = Counter(s["domain"] for s in data["subcompetencies"])
    print(f"[OK] {args.pdf} → {args.output}")
    print(f"     {n} subcompetencies, dist={dict(dist)}")


if __name__ == "__main__":
    main()
