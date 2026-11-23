#!/usr/bin/env python3
"""Give every film in the bank the full annotation a report can be written from.

The manifest used to carry one statement per film. That was enough to *retrieve*
a film for a finished report but not to *write* a report that matches the film:
71% of the bank's ECGs carry other cardiologist statements the manifest dropped
(an "inferior MI" tracing that also shows an anteroseptal infarct and a
fascicular block), no ECG recorded the axis or infarction stadium, and no chest
film recorded which devices are on it or which side the finding is on.

This tool writes an ``annotation`` block onto each entry from two sources:

* **PTB-XL, programmatic** (authoritative): every SCP statement with its
  likelihood and kind, ``heart_axis``, ``infarction_stadium``, ``pacemaker``,
  ``extra_beats``, the signal-quality flags and the cardiologist's original
  free-text ``report``. Other diagnostic statements (likelihood >= 50) become
  ``co_findings`` with ``co_findings_validated: true`` so the retrieval gate
  treats them as part of the film's claim.
* **Sidecars written by the review workflow** (``image_bank/annotations/``):
  zh-TW translations of the PTB-XL reports verified against the codes, and the
  three-reader chest-film reads (side, zone, size, devices, other findings,
  suitability). A chest film's side is recorded as *provisional* — derived, not
  clinician-reviewed — and the page says so.

``attributes`` are recomputed from the full claim (statement + validated
co-findings) via ``util.image_bank.claim_attributes`` so the gate and the
annotation cannot disagree. Reviewer-set laterality is never overwritten.

Usage (PowerShell, repo root):
  python tools/enrich_image_bank.py --db ptbxl_database.csv --scp scp_statements.csv
  python tools/enrich_image_bank.py --db ptbxl_database.csv --scp scp_statements.csv --dry-run
"""
import argparse
import ast
import csv
import glob
import io
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_common import load_manifest, save_manifest  # noqa: E402
from util import image_bank as ib  # noqa: E402

AXIS_ZH = {"MID": "電軸正常", "LAD": "電軸左偏", "ALAD": "電軸極度左偏", "RAD": "電軸右偏",
           "ARAD": "電軸極度右偏", "AXR": "電軸右偏", "AXL": "電軸左偏", "SAG": "電軸不定（矢狀）"}
STADIUM_ZH = {"Stadium I": "急性期", "Stadium I-II": "急性至亞急性期", "Stadium II": "亞急性期",
              "Stadium II-III": "亞急性至陳舊性", "Stadium III": "陳舊性"}
CO_MIN_LIKELIHOOD = 50.0

# Fixed zh-TW names for all 71 PTB-XL SCP statements (scp_statements.csv). The
# review workflow's verified translations override these per record; this table
# guarantees every statement has a Chinese name even before that runs.
SCP_ZH = {
    # diagnostic
    "NORM": "正常心電圖", "NDT": "非診斷性 T 波異常", "NST_": "非特異性 ST 變化", "DIG": "毛地黃效應",
    "LNGQT": "QT 間期延長", "IMI": "下壁心肌梗塞", "ASMI": "前間壁心肌梗塞", "LVH": "左心室肥厚",
    "LAFB": "左前分支阻滯", "ISC_": "非特異性缺血", "IRBBB": "不完全性右束支傳導阻滯", "1AVB": "一度房室傳導阻滯",
    "IVCD": "非特異性心室內傳導障礙", "ISCAL": "前側壁導程缺血", "CRBBB": "完全性右束支傳導阻滯",
    "CLBBB": "完全性左束支傳導阻滯", "ILMI": "下側壁心肌梗塞", "LAO/LAE": "左心房擴大", "AMI": "前壁心肌梗塞",
    "ALMI": "前側壁心肌梗塞", "ISCIN": "下壁導程缺血", "INJAS": "前間壁導程心內膜下損傷", "LMI": "側壁心肌梗塞",
    "ISCIL": "下側壁導程缺血", "LPFB": "左後分支阻滯", "ISCAS": "前間壁導程缺血", "INJAL": "前側壁導程心內膜下損傷",
    "ISCLA": "側壁導程缺血", "RVH": "右心室肥厚", "ANEUR": "心室瘤之 ST-T 變化", "RAO/RAE": "右心房擴大",
    "EL": "電解質異常或藥物影響", "WPW": "WPW 症候群", "ILBBB": "不完全性左束支傳導阻滯",
    "IPLMI": "下後側壁心肌梗塞", "ISCAN": "前壁導程缺血", "IPMI": "下後壁心肌梗塞", "SEHYP": "中隔肥厚",
    "INJIN": "下壁導程心內膜下損傷", "INJLA": "側壁導程心內膜下損傷", "PMI": "後壁心肌梗塞",
    "3AVB": "三度房室傳導阻滯", "INJIL": "下側壁導程心內膜下損傷", "2AVB": "二度房室傳導阻滯",
    # form
    "ABQRS": "QRS 波異常", "PVC": "心室早期收縮", "STD_": "非特異性 ST 壓低", "VCLVH": "左心室肥厚之電壓標準",
    "QWAVE": "病理性 Q 波", "LOWT": "T 波低平", "NT_": "非特異性 T 波變化", "PAC": "心房早期收縮",
    "LPR": "PR 間期延長", "INVT": "T 波倒置", "LVOLT": "QRS 低電壓", "HVOLT": "QRS 高電壓",
    "TAB_": "T 波異常", "STE_": "非特異性 ST 上升", "PRC(S)": "期外收縮",
    # rhythm
    "SR": "竇性心律", "AFIB": "心房顫動", "STACH": "竇性心搏過速", "SARRH": "竇性心律不整",
    "SBRAD": "竇性心搏過緩", "PACE": "節律器心律", "SVARR": "室上性心律不整", "BIGU": "二聯律",
    "AFLT": "心房撲動", "SVTAC": "室上性心搏過速", "PSVT": "陣發性室上性心搏過速", "TRIGU": "三聯律",
}


# ---------------------------------------------------------------- PTB-XL
def read_ptbxl(db_path, scp_path):
    db = {r["ecg_id"]: r for r in csv.DictReader(io.open(db_path, encoding="utf-8-sig"))}
    scp = {}
    for r in csv.DictReader(io.open(scp_path, encoding="utf-8-sig")):
        # the SCP code sits in an unnamed first column (header is empty)
        code = (r.get("") if "" in r else r[next(iter(r))]).strip()
        kind = "diagnostic" if r.get("diagnostic") == "1.0" else (
            "form" if r.get("form") == "1.0" else ("rhythm" if r.get("rhythm") == "1.0" else "other"))
        scp[code] = {"description": r.get("description") or code, "kind": kind,
                     "class": r.get("diagnostic_class") or "", "subclass": r.get("diagnostic_subclass") or ""}
    return db, scp


def _flag(v):
    v = (v or "").strip()
    return v if v else ""


def ptbxl_annotation(entry, db, scp):
    r = db.get(str(entry.get("ecg_id")))
    if r is None:
        return None
    codes = ast.literal_eval(r["scp_codes"]) if r.get("scp_codes") else {}
    statements = []
    for code, lik in codes.items():
        meta = scp.get(code, {"description": code, "kind": "other", "class": "", "subclass": ""})
        statements.append({"code": code, "likelihood": float(lik), "kind": meta["kind"],
                           "description": meta["description"], "class": meta["class"],
                           "zh": SCP_ZH.get(code, meta["description"])})
    statements.sort(key=lambda s: (-s["likelihood"], s["code"]))
    own = entry.get("statement_code")
    co = [s["description"] for s in statements
          if s["kind"] == "diagnostic" and s["code"] != own and s["likelihood"] >= CO_MIN_LIKELIHOOD]
    stadium = _flag(r.get("infarction_stadium1"))
    if stadium.lower() == "unknown":
        stadium = ""
    ann = {
        "source": "ptbxl",
        "statements": statements,
        "heart_axis": _flag(r.get("heart_axis")),
        "axis_zh": AXIS_ZH.get(_flag(r.get("heart_axis")), ""),
        "infarction_stadium": stadium,
        "infarction_stadium2": _flag(r.get("infarction_stadium2")),
        "stadium_zh": STADIUM_ZH.get(stadium, ""),
        "pacemaker": _flag(r.get("pacemaker")),
        "extra_beats": _flag(r.get("extra_beats")),
        "quality": {k: _flag(r.get(k)) for k in
                    ("baseline_drift", "static_noise", "burst_noise", "electrodes_problems")},
        "source_report": _flag(r.get("report")),
        "validated_by_human": _flag(r.get("validated_by_human")) == "True",
        "recording_device": _flag(r.get("device")),
    }
    return ann, co


def merge_ecg_sidecars(entries_by_id, ecg_dir):
    """Fold the workflow's verified translations into the PTB-XL annotations."""
    merged = 0
    for path in sorted(glob.glob(os.path.join(ecg_dir, "batch_*.json"))):
        try:
            rows = json.load(io.open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("  ! skipping %s: %s" % (path, exc))
            continue
        for row in rows if isinstance(rows, list) else []:
            e = entries_by_id.get(row.get("id"))
            if not e or not isinstance(e.get("annotation"), dict):
                continue
            ann = e["annotation"]
            ann["source_report_zh"] = row.get("source_report_zh") or ""
            ann["report_language"] = row.get("language") or ""
            ann["report_consistent_with_codes"] = bool(row.get("consistent_with_codes", True))
            ann["report_issues"] = row.get("issues") or ""
            if row.get("axis_zh"):
                ann["axis_zh"] = row["axis_zh"]
            if row.get("stadium_zh"):
                ann["stadium_zh"] = row["stadium_zh"]
            zh = {s.get("code"): s.get("zh") for s in (row.get("statements_zh") or []) if isinstance(s, dict)}
            for s in ann.get("statements", []):
                if zh.get(s["code"]):
                    s["zh"] = zh[s["code"]]
            merged += 1
    return merged


# ---------------------------------------------------------------- NIH / CXR
def merge_cxr_sidecars(entries_by_id, cxr_dir):
    """Fold the three-reader reads in; record the side as provisional."""
    merged = conflicts = sided = 0
    for path in sorted(glob.glob(os.path.join(cxr_dir, "*.json"))):
        try:
            read = json.load(io.open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("  ! skipping %s: %s" % (path, exc))
            continue
        e = entries_by_id.get(read.get("id") or os.path.splitext(os.path.basename(path))[0])
        if not e:
            continue
        ann = {k: read.get(k) for k in (
            "reviewed_by", "view", "technical_quality", "target", "laterality_sources", "agreement",
            "devices", "other_findings", "treatment_evidence", "first_presentation_suitable",
            "suitability_reason", "description_zh", "label_concern")}
        ann["source"] = "nih+ai-vision"
        e["annotation"] = ann
        merged += 1

        attrs = e.setdefault("attributes", ib.claim_attributes(*ib.claim_terms(e)))
        if attrs.get("laterality_reviewed"):
            continue                      # a human's decision stands
        tgt = read.get("target") if isinstance(read.get("target"), dict) else {}
        side = tgt.get("laterality")
        agreement = read.get("agreement")
        srcs = read.get("laterality_sources") if isinstance(read.get("laterality_sources"), dict) else {}
        bbox_side = srcs.get("bbox")
        if side in ("right", "left", "bilateral") and agreement in ("full", "partial") \
                and (not bbox_side or bbox_side in (side, "none", "", None)):
            attrs["laterality"] = side
            attrs["laterality_source"] = "bbox+ai-vision" if bbox_side else "ai-vision"
            sided += 1
        else:
            attrs["laterality"] = None
            attrs.pop("laterality_source", None)
            if agreement == "conflict":
                conflicts += 1
    return merged, sided, conflicts


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(ROOT, "ptbxl_database.csv"))
    ap.add_argument("--scp", default=os.path.join(ROOT, "scp_statements.csv"))
    ap.add_argument("--bank-dir", default=None)
    ap.add_argument("--annotations-dir", default=None, help="default: <bank>/annotations")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bank = args.bank_dir or ib.bank_dir()
    ann_dir = args.annotations_dir or os.path.join(bank, "annotations")
    man = load_manifest(bank)
    entries = man["entries"]
    by_id = {e.get("id"): e for e in entries}

    # 1) PTB-XL programmatic enrichment
    n_ecg = n_co = 0
    if os.path.isfile(args.db) and os.path.isfile(args.scp):
        db, scp = read_ptbxl(args.db, args.scp)
        for e in entries:
            if e.get("modality") != "ECG":
                continue
            res = ptbxl_annotation(e, db, scp)
            if res is None:
                continue
            ann, co = res
            keep = e.get("annotation") if isinstance(e.get("annotation"), dict) else {}
            # keep translation fields from an earlier merge
            for k in ("source_report_zh", "report_language", "report_consistent_with_codes", "report_issues"):
                if keep.get(k) is not None:
                    ann[k] = keep[k]
            old_zh = {s.get("code"): s.get("zh") for s in (keep.get("statements") or []) if isinstance(s, dict)}
            for s in ann["statements"]:
                if old_zh.get(s["code"]):
                    s["zh"] = old_zh[s["code"]]
            e["annotation"] = ann
            e["co_findings"] = co
            e["co_findings_validated"] = True
            n_ecg += 1
            n_co += 1 if co else 0
        print("PTB-XL: annotated %d ECG entries; %d carry validated co-findings" % (n_ecg, n_co))
    else:
        print("PTB-XL CSVs not found (%s / %s): skipping ECG enrichment" % (args.db, args.scp))

    # 2) sidecars
    m_ecg = merge_ecg_sidecars(by_id, os.path.join(ann_dir, "ecg"))
    print("ECG sidecars: merged %d verified translations" % m_ecg)
    m_cxr, sided, conflicts = merge_cxr_sidecars(by_id, os.path.join(ann_dir, "cxr"))
    print("CXR sidecars: merged %d reads; provisional side recorded on %d; %d conflicts left unset"
          % (m_cxr, sided, conflicts))

    # 3) attributes from the full claim, preserving reviewer/provisional side
    n_attr = 0
    for e in entries:
        old = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
        fresh = ib.claim_attributes(*ib.claim_terms(e))
        for k in ("laterality", "laterality_reviewed", "laterality_source"):
            if old.get(k) is not None:
                fresh[k] = old[k]
        if fresh != old:
            e["attributes"] = fresh
            n_attr += 1
    print("attributes recomputed from statement + validated co-findings: %d changed" % n_attr)

    # 4) honest summary
    ecg = [e for e in entries if e.get("modality") == "ECG"]
    if ecg:
        c = Counter(len(ib.validated_co_findings(e)) for e in ecg)
        print("ECG validated co-finding count distribution: %s" % dict(sorted(c.items())))
        st = Counter((e.get("annotation") or {}).get("stadium_zh") or "(未標)" for e in ecg
                     if (e.get("attributes") or {}).get("lesion") == "infarction")
        print("ECG infarct films by stadium: %s" % dict(st))
    cxr = [e for e in entries if e.get("modality") == "CXR"]
    if cxr:
        suit = Counter(str((e.get("annotation") or {}).get("first_presentation_suitable")) for e in cxr)
        print("CXR first-presentation suitability: %s" % dict(suit))
        dev = Counter(bool((e.get("annotation") or {}).get("devices")) for e in cxr)
        print("CXR films with devices: %s" % dict(dev))

    if args.dry_run:
        print("--dry-run: manifest NOT written")
        return 0
    save_manifest(bank, man)
    print("manifest saved: %s" % os.path.join(bank, "manifest.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
