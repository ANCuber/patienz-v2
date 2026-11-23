#!/usr/bin/env python3
"""Smoke-test the image bank end to end without Streamlit or an API key.

Calls util.image_bank.find_image() with exactly the arguments page/examination.py
builds in _image_query(), so a pass here means the real page would behave the
same way.  Half the cases are negative controls: the retrieval invariant is
"never show a misleading image", so a case that *must* return None is as
important as one that must return a film.

Usage (PowerShell, from the repo root):
  python tools/check_image_bank.py                 # inventory + all scenarios
  python tools/check_image_bank.py --verbose       # also print the caption/provenance
  python tools/check_image_bank.py --modality CXR --report "胸部X光：右側氣胸。" \
      --abnormal --age 24 --sex M                  # ad-hoc single query
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from util import image_bank  # noqa: E402

ECG_TERMS = ["ECG", "EKG", "心電圖"]
CXR_TERMS = ["Chest X-ray", "CXR", "胸部X光"]

# (name, modality, abnormal?, report text, age, sex, item terms, expectation)
#   expectation None      -> find_image must return nothing
#   expectation "text"    -> the matched entry's findings must contain it
#   expectation "!text"   -> a film must come back, but NOT one claiming that
CASES = [
    # ---------- ECG: should surface a film ----------
    ("ECG 心房顫動", "ECG", True,
     "心電圖：心房顫動，心室率約 110 次/分，無明顯 ST 段上升。",
     72, "F", ECG_TERMS, "atrial fibrillation"),
    ("ECG 左束支傳導阻滯", "ECG", True,
     "心電圖：竇性心律，QRS 增寬達 140 ms，呈左束支傳導阻滯型態。",
     68, "M", ECG_TERMS, "left bundle branch block"),
    ("ECG 前間壁心肌梗塞", "ECG", True,
     "心電圖：V1-V4 導程 ST 段上升並出現 Q 波，符合前間壁心肌梗塞。",
     58, "M", ECG_TERMS, "myocardial infarction"),
    ("ECG 竇性心搏過速", "ECG", True,
     "心電圖：竇性心搏過速，心率 122 次/分。",
     31, "F", ECG_TERMS, "sinus tachycardia"),
    ("ECG 正常", "ECG", False,
     "心電圖：竇性心律，心率 72 次/分，PR、QRS、QT 間期均正常，各導程未見異常。",
     29, "M", ECG_TERMS, "normal"),

    # ---------- CXR: should surface a film ----------
    ("CXR 氣胸", "CXR", True,
     "胸部X光：右側氣胸，肺尖可見臟層肋膜線，該側肺紋理消失。",
     24, "M", CXR_TERMS, "pneumothorax"),
    ("CXR 肋膜積水", "CXR", True,
     "胸部X光：左側肋膜積水，左側肋膈角變鈍。",
     71, "F", CXR_TERMS, "pleural effusion"),
    ("CXR 心臟肥大", "CXR", True,
     "胸部X光：心臟肥大，心胸比約 0.58，肺野未見浸潤。",
     66, "M", CXR_TERMS, "cardiomegaly"),
    ("CXR 肺炎", "CXR", True,
     "胸部X光：右下肺葉可見肺炎變化。",
     45, "F", CXR_TERMS, "pneumonia"),
    ("CXR 肺結節", "CXR", True,
     "胸部X光：左上肺野可見一顆約 1.2 公分的肺結節。",
     57, "M", CXR_TERMS, "nodule"),

    # ---------- localization: the film must not claim another wall ----------
    ("ECG 下壁 STEMI", "ECG", True,
     "心電圖：竇性心搏過速 110 bpm。II、III、aVF 導程 ST 段上升 3 mm，aVL 呈對側性 ST 壓低。符合急性下壁心肌梗塞。",
     20, "F", ECG_TERMS, "inferior myocardial infarction"),
    ("ECG 前間壁 STEMI（導程）", "ECG", True,
     "心電圖：V1-V4 導程 ST 段上升並出現病理性 Q 波。符合急性前間壁心肌梗塞。",
     58, "M", ECG_TERMS, "anteroseptal myocardial infarction"),
    ("ECG 側壁缺血", "ECG", True,
     "心電圖：I、aVL、V5、V6 導程 T 波倒置。考慮側壁心肌缺血。",
     62, "M", ECG_TERMS, "lateral"),
    ("ECG 不完全性右束支阻滯", "ECG", True,
     "心電圖：竇性心律，呈不完全性右束支傳導阻滯。",
     44, "M", ECG_TERMS, "incomplete right bundle branch block"),
    ("安全閘：下壁報告不可帶出前壁片", "ECG", True,
     "心電圖：II、III、aVF ST 段上升。符合急性下壁心肌梗塞。",
     20, "F", ECG_TERMS, "!anterior"),
    ("安全閘：報告未定位就不給帶定位的片", "ECG", True,
     "心電圖：ST 段上升合併 T 波變化，符合急性心肌梗塞變化。",
     20, "F", ECG_TERMS, "!infarction"),
    ("安全閘：缺血報告不可帶出梗塞片", "ECG", True,
     "心電圖：ST-T 變化，考慮心肌缺血。",
     20, "F", ECG_TERMS, "!infarction"),

    # ---------- negative controls: must return nothing ----------
    # published without a clinician review: they display, carrying the red
    # un-reviewed-label warning (see util.image_bank.review_status)
    ("CXR 正常胸片", "CXR", False,
     "胸部X光：兩側肺野清晰，心影大小正常，肋膈角銳利。",
     40, "F", CXR_TERMS, "no acute cardiopulmonary finding"),
    ("CXR 肺實變", "CXR", True,
     "胸部X光：右上肺葉實變合併空氣支氣管徵。",
     63, "M", CXR_TERMS, "consolidation"),
    ("安全閘：心包積液不可帶出肋膜積水片", "CXR", True,
     "胸部X光：心影呈燒瓶狀，疑似心包積液。",
     55, "F", CXR_TERMS, None),
    ("安全閘：報告所見不在影像庫", "CXR", True,
     "胸部X光：胸骨術後鋼絲固定良好，未見其他異常。",
     70, "M", CXR_TERMS, None),
    ("安全閘：ECG 報告不可帶出 CXR 片", "ECG", True,
     "胸部X光：右側氣胸，肺尖塌陷。",
     24, "M", ECG_TERMS, None),
    ("安全閘：CXR 報告不可帶出 ECG 片", "CXR", True,
     "心電圖：心房顫動，心室率 110 次/分。",
     72, "F", CXR_TERMS, None),
    ("安全閘：無法判讀的報告", "ECG", True,
     "心電圖：訊號干擾嚴重，無法判讀。",
     50, "M", ECG_TERMS, None),
]


def inventory(man):
    entries = man.get("entries", [])
    print("manifest : %s" % os.path.join(image_bank.bank_dir(), "manifest.json"))
    print("entries  : %d" % len(entries))
    missing = 0
    for mod in sorted({e.get("modality") for e in entries if e.get("modality")}):
        rows = [e for e in entries if e.get("modality") == mod]
        ver = [e for e in rows if e.get("verified")]
        norm = Counter(e.get("normality") for e in ver)
        gone = [e for e in rows if not os.path.isfile(image_bank.image_path(e))]
        missing += len(gone)
        unchecked = sum(1 for e in ver if image_bank.review_status(e)
                        in ("nlp-mined", "radiologist-bbox", "unknown"))
        print("  %-4s %4d total | %4d retrievable (%d abnormal / %d normal) | %3d hidden"
              " | %3d label unreviewed"
              % (mod, len(rows), len(ver), norm.get("abnormal", 0),
                 norm.get("normal", 0), len(rows) - len(ver), unchecked))
        if gone:
            print("       %d entr(ies) point at a missing file, e.g. %s"
                  % (len(gone), gone[0]["file"]))
    synth = [e for e in entries if e.get("synthetic")]
    print("  synthetic (AI-generated) entries: %d  <- must be 0" % len(synth))
    return missing == 0 and not synth


def run_case(case, man, verbose):
    name, mod, abnormal, report, age, sex, terms, expect = case
    hit = image_bank.find_image(
        mod, has_abnormal=abnormal, report_text=report, item_terms=terms,
        patient_age=age, patient_sex=sex, manifest=man,
    )
    got = ", ".join(hit.get("findings") or []) if hit else "(none)"
    if expect is None:
        ok = hit is None
        want = "(none)"
    elif expect.startswith("!"):
        # a film is expected, but it must not claim this
        forbidden = expect[1:].lower()
        ok = bool(hit) and not any(forbidden in f.lower() for f in (hit.get("findings") or []))
        want = "not " + forbidden
    else:
        ok = bool(hit) and any(expect.lower() in f.lower() for f in (hit.get("findings") or []))
        want = expect
    print("  [%s] %-38s want=%-22s got=%s" % ("PASS" if ok else "FAIL", name, want, got))
    if hit and (verbose or not ok):
        info = image_bank.describe(hit)
        print("        id=%s  %s/%s  %s" % (hit["id"], hit.get("sex"), hit.get("age"),
                                            "verified" if hit.get("verified") else "UNVERIFIED"))
        print("        caption   : %s" % info.get("caption"))
        print("        provenance: %s" % info.get("provenance"))
    return ok


def demo_tiebreak(man):
    """The same query for different patients should prefer a demographically
    closer film -- proof the age/sex ranking is live, not decorative."""
    report = "胸部X光：左側肋膜積水，左側肋膈角變鈍。"
    print("\n人口學排序（同一份報告，不同病人）：")
    seen = []
    for age, sex in ((9, "F"), (28, "M"), (50, "F"), (70, "M"), (88, "F")):
        hit = image_bank.find_image("CXR", has_abnormal=True, report_text=report,
                                    item_terms=CXR_TERMS, patient_age=age,
                                    patient_sex=sex, manifest=man)
        got = "%s/%s %s" % (hit.get("sex"), hit.get("age"), hit["id"]) if hit else "(none)"
        print("  病人 %2d 歲 %s -> %s" % (age, sex, got))
        seen.append(hit["id"] if hit else None)
    distinct = len({s for s in seen if s})
    print("  -> %d 個不同的病人分層各自取到不同影像" % distinct)
    return distinct > 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--modality", help="ad-hoc query: ECG / CXR / CT ...")
    ap.add_argument("--report", default="", help="ad-hoc query: report text")
    ap.add_argument("--abnormal", action="store_true", help="ad-hoc query: abnormal result")
    ap.add_argument("--age", type=float)
    ap.add_argument("--sex")
    args = ap.parse_args()

    if not image_bank.enabled():
        print("image bank is DISABLED (PATIENZ_DISABLE_IMAGE_BANK is set)")
        return 1
    man = image_bank.load_manifest()

    if args.modality:
        terms = CXR_TERMS if args.modality.upper() == "CXR" else ECG_TERMS
        hit = image_bank.find_image(args.modality.upper(), has_abnormal=args.abnormal,
                                    report_text=args.report, item_terms=terms,
                                    patient_age=args.age, patient_sex=args.sex, manifest=man)
        if not hit:
            print("no image (the report text did not clear the safety gate)")
            return 0
        info = image_bank.describe(hit)
        print("id         : %s" % hit["id"])
        print("findings   : %s" % ", ".join(hit.get("findings") or []))
        print("patient    : %s / %s" % (hit.get("sex"), hit.get("age")))
        print("file       : %s" % info.get("path"))
        print("caption    : %s" % info.get("caption"))
        print("provenance : %s" % info.get("provenance"))
        return 0

    print("=" * 74)
    ok_inv = inventory(man)
    print("=" * 74)
    print("檢索情境（出片／定位正確／安全閘）：")
    results = [run_case(c, man, args.verbose) for c in CASES]
    ok_demo = demo_tiebreak(man)
    print("=" * 74)
    passed = sum(1 for r in results if r)
    print("scenarios: %d/%d passed | inventory: %s | demographic ranking: %s"
          % (passed, len(results), "ok" if ok_inv else "PROBLEM",
             "ok" if ok_demo else "PROBLEM"))
    return 0 if passed == len(results) and ok_inv and ok_demo else 1


if __name__ == "__main__":
    sys.exit(main())
