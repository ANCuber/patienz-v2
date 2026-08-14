#!/usr/bin/env python3
"""Build a broad, demographic-spread PTB-XL ECG image bank (§6-A).

Goal (per the agreed plan): give **as many of PTB-XL's 71 SCP statements as the
data allows** at least one real, human-validated 12-lead ECG, and — where the
data allows — spread each across sex x age-band cells so the demographic-proximity
retrieval in ``util.image_bank.find_image`` has something close to pick. What the
data genuinely cannot cover (rare statements like PMI/2AVB/PRC(S) with only a
handful of records in the whole dataset) is reported honestly, not faked.

Pipeline: read ``ptbxl_database.csv`` + ``scp_statements.csv`` -> for each
statement pick the best human-validated records across sex x age-band cells ->
render each as a clinical 12-lead ECG (reusing ``render_ptbxl_ecg``) -> write
manifest entries (with age/sex/statement/subclass, ``verified: true``) -> emit a
coverage report.

Requires: ``pip install wfdb matplotlib pandas numpy`` and both CSVs (open
access, no login):
    curl -L -o ptbxl_database.csv  https://physionet.org/files/ptb-xl/1.0.3/ptbxl_database.csv
    curl -L -o scp_statements.csv  https://physionet.org/files/ptb-xl/1.0.3/scp_statements.csv

Typical run (streams only the selected records from PhysioNet):
    python tools/build_ptbxl_bank.py --db ptbxl_database.csv --scp scp_statements.csv

Then eyeball a sample of image_bank/ecg/*.png and read image_bank/ptbxl_coverage.md.
NOTE: entries are written ``verified: true`` because they are cardiologist-annotated
and human-validated; the abnormal gate still only shows one when its finding is
evidenced in the exam report. Pass --unverified to require your own manual review
(entries land verified:false and stay hidden until you flip them).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_ptbxl_ecg as rp  # noqa: E402  (sibling tool; reuse render/parse)

SOURCE = "PTB-XL (PhysioNet)"
SOURCE_URL = "https://physionet.org/content/ptb-xl/1.0.3/"
LICENSE = "CC BY 4.0"
ATTRIBUTION = "Wagner et al., PTB-XL, PhysioNet"


# ---------- statement metadata + finding mapping (pure) ----------
def load_statement_meta(scp_df):
    """code -> {description, category, subclass, normality} for all 71 rows."""
    meta = {}
    for _, r in scp_df.iterrows():
        code = str(r[scp_df.columns[0]]).strip()
        cats = []
        for c in ("diagnostic", "form", "rhythm"):
            try:
                if float(r.get(c, 0) or 0) == 1.0:
                    cats.append(c)
            except (TypeError, ValueError):
                pass
        meta[code] = {
            "description": str(r.get("description", "") or "").strip(),
            "category": "+".join(cats) or "other",
            "subclass": (str(r.get("diagnostic_subclass", "")).strip()
                         if str(r.get("diagnostic_subclass", "")).strip().lower() != "nan" else ""),
            # NORM and SR (sinus rhythm) are clean-normal tracings; everything
            # else is a finding to be gated as abnormal.
            "normality": "normal" if code in ("NORM", "SR") else "abnormal",
        }
    return meta


def umbrella_terms(code, sub, desc):
    """Extra, more-searchable finding terms so a Chinese/English report can
    evidence the film (these route through grading_normalize.surface_forms)."""
    d = (desc or "").lower()
    t = []
    if sub in ("IMI", "AMI", "LMI", "PMI") or "infarction" in d:
        t.append("myocardial infarction")
    if sub in ("ISCA", "ISCI", "ISC_") or "ischemic" in d or "ischaemic" in d:
        t.append("myocardial ischemia")
    if code in ("CLBBB", "ILBBB") or "left bundle" in d:
        t.append("left bundle branch block")
    if code in ("CRBBB", "IRBBB") or "right bundle" in d:
        t.append("right bundle branch block")
    if code in ("1AVB", "2AVB", "3AVB") or sub == "_AVB" or "av block" in d:
        t.append("atrioventricular block")
    if code == "LVH" or "left ventricular hypertrophy" in d:
        t.append("left ventricular hypertrophy")
    if code == "RVH" or "right ventricular hypertrophy" in d:
        t.append("right ventricular hypertrophy")
    if sub == "LAO/LAE":
        t.append("left atrial enlargement")
    if sub == "RAO/RAE":
        t.append("right atrial enlargement")
    t += {
        "AFIB": ["atrial fibrillation"], "AFLT": ["atrial flutter"],
        "STACH": ["sinus tachycardia"], "SBRAD": ["sinus bradycardia"],
        "PACE": ["paced rhythm"], "PVC": ["premature ventricular complex"],
        "PAC": ["atrial premature complex"], "WPW": ["wolff-parkinson-white"],
        "LNGQT": ["long QT"], "STE_": ["ST elevation"], "STD_": ["ST depression"],
    }.get(code, [])
    return t


def findings_for(code, meta):
    desc = meta["description"] or code
    umb = umbrella_terms(code, meta["subclass"], desc)
    findings, seen = [], set()
    for term in [desc] + umb:
        k = term.lower()
        if k not in seen:
            seen.add(k)
            findings.append(term)
    return {
        "findings": findings,
        "disease_keywords": umb,
        "normality": meta["normality"],
        "caption": f"12-lead ECG — {desc}",
    }


# ---------- selection across sex x age-band cells (pure) ----------
def parse_bands(spec):
    bounds = sorted({int(x) for x in str(spec).split(",") if str(x).strip() != ""})
    bounds = [b for b in bounds if b >= 0]
    if not bounds or bounds[0] != 0:
        bounds = [0] + bounds
    return bounds  # e.g. [0,40,60] -> bands [0,40),[40,60),[60,inf)


def age_band(age, bounds):
    try:
        a = float(age)
    except (TypeError, ValueError):
        return None
    idx = 0
    for i, b in enumerate(bounds):
        if a >= b:
            idx = i
    return idx


def _sex_mf(v):
    s = str(v).strip()
    if s in ("0", "0.0", "M", "male", "Male"):
        return "M"
    if s in ("1", "1.0", "F", "female", "Female"):
        return "F"
    return None


def _is_clean(codes):
    """No diagnostic/abnormal statement carried with meaningful likelihood — so a
    NORM/SR tracing is genuinely a clean normal, not a normal rhythm co-existing
    with a pathology."""
    return not any(k not in ("NORM", "SR") and (v or 0) >= 50 for k, v in codes.items())


def _to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_index(df):
    """Single pass over the dataframe → ``code -> [record dict]``.

    Parsing ``scp_codes`` once (instead of once per statement) turns an
    O(codes x rows) scan into O(rows), which matters at 21,799 rows x 71 codes.
    Each record dict carries everything selection/rendering needs.
    """
    from collections import defaultdict
    index = defaultdict(list)
    for _, r in df.iterrows():
        parsed = rp.parse_scp(r.get("scp_codes", ""))
        if not parsed:
            continue
        age = r.get("age")
        rec = {
            "parsed": parsed,
            "human": str(r.get("validated_by_human", "")).strip().lower() in ("true", "1"),
            "sex": _sex_mf(r.get("sex")),
            "age": _to_float(age, None) if str(age).strip() != "" else None,
            "fold": _to_int(r.get("strat_fold")),
            "noise": _to_float(r.get("static_noise")),
            "ecg_id": str(r.get("ecg_id", "")).strip(),
            "ecg_id_int": _to_int(r.get("ecg_id")),
            "row": r,
        }
        for code in parsed:
            index[code].append(rec)
    return index


def select_for_code(recs, code, meta, per_cell, bounds, min_likelihood):
    """Pick up to ``per_cell`` best human-validated records per (sex, age-band)
    cell for one statement, from ``recs`` = the index bucket for ``code``.

    Returns (selected, info). selected: [{rec, cell, likelihood}].
    """
    from collections import defaultdict
    is_dx = "diagnostic" in meta["category"]
    bycell = defaultdict(list)
    n_available = 0
    for rec in recs:
        codes = rec["parsed"]
        lk = codes.get(code, 0) or 0
        if code in ("NORM", "SR"):
            # clean-normal tracing only (no co-existing pathology)
            if not _is_clean(codes):
                continue
        elif is_dx:
            # diagnostic statements carry a cardiologist likelihood → require it
            if lk < min_likelihood:
                continue
        # else: pure form / rhythm statements are recorded with likelihood 0
        # ("unknown"); presence + human validation is the quality signal.
        if not rec["human"] or rec["sex"] is None:
            continue
        band = age_band(rec["age"], bounds)
        if band is None:
            continue
        n_available += 1
        qkey = (0 if rec["fold"] in (9, 10) else 1, -lk, rec["noise"], rec["ecg_id_int"])
        bycell[(rec["sex"], band)].append((qkey, rec, lk))

    selected = []
    for cell, items in bycell.items():
        items.sort(key=lambda x: x[0])
        for qkey, rec, lk in items[:per_cell]:
            selected.append({"rec": rec, "cell": cell, "likelihood": lk})

    return selected, {
        "n_available": n_available,
        "cells_available": len(bycell),
        "cells_total": 2 * len(bounds),
        "n_selected": len(selected),
    }


def coverage_status(info):
    if info["n_available"] == 0:
        return "none"
    if info["cells_available"] >= info["cells_total"]:
        return "ok"
    return "partial"


# ---------- manifest I/O ----------
def load_manifest(bank_dir):
    path = os.path.join(bank_dir, "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("entries"), list):
            data["entries"] = []
        return data
    except (OSError, ValueError):
        return {"schema_version": 1, "entries": []}


def save_manifest(bank_dir, data):
    path = os.path.join(bank_dir, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def make_entry(code, meta, sel, verified):
    rec = sel["rec"]
    ecg_id = rec["ecg_id"]
    fm = findings_for(code, meta)
    entry_id = f"ptbxl-{code.replace('/', '-').replace('(', '').replace(')', '')}-{ecg_id}"
    rel = os.path.join("ecg", entry_id + ".png").replace("\\", "/")
    age = int(rec["age"]) if rec["age"] is not None else None
    return {
        "id": entry_id,
        "file": rel,
        "modality": "ECG",
        "normality": fm["normality"],
        "findings": fm["findings"],
        "disease_keywords": fm["disease_keywords"],
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "deidentified": True,
        "synthetic": False,
        "verified": bool(verified),
        "statement_code": code,
        "statement_category": meta["category"],
        "diagnostic_subclass": meta["subclass"],
        "age": age,
        "sex": rec["sex"],
        "ecg_id": ecg_id,
        "likelihood": sel["likelihood"],
        "caption": fm["caption"],
    }


def write_coverage_report(path, rows, bounds):
    band_lbl = []
    for i, b in enumerate(bounds):
        hi = bounds[i + 1] if i + 1 < len(bounds) else None
        band_lbl.append(f"{b}-{hi}" if hi else f"{b}+")
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_any = sum(1 for r in rows if r["info"]["n_selected"] > 0)
    subs = {r["meta"]["subclass"] for r in rows if r["meta"]["subclass"]}
    subs_cov = {r["meta"]["subclass"] for r in rows
                if r["meta"]["subclass"] and r["info"]["n_selected"] > 0}
    lines = [
        "# PTB-XL 影像庫覆蓋報告（§6-A）",
        "",
        f"- 目標分層：性別(2) × 年齡帶({'/'.join(band_lbl)}) = {2*len(bounds)} 個 cell/statement",
        f"- 有 ≥1 張影像的 statement：**{n_any} / {len(rows)}**",
        f"- 各分層全覆蓋(ok)的 statement：**{n_ok} / {len(rows)}**",
        f"- 有涵蓋的診斷子類別：**{len(subs_cov)} / {len(subs)}**",
        "",
        "| code | 類別 | 子類別 | 說明 | 可用筆數 | 已選 | cell 覆蓋 | 狀態 |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    order = {"none": 0, "partial": 1, "ok": 2}
    for r in sorted(rows, key=lambda x: (order[x["status"]], -x["info"]["n_available"])):
        i = r["info"]
        lines.append(
            f"| `{r['code']}` | {r['meta']['category']} | {r['meta']['subclass'] or '—'} | "
            f"{r['meta']['description']} | {i['n_available']} | {i['n_selected']} | "
            f"{i['cells_available']}/{i['cells_total']} | {r['status']} |")
    lines += [
        "",
        "> `none` = 全庫無合格(人工複核＋信心達標)紀錄，資料不足；`partial` = 只覆蓋部分",
        "> 分層；`ok` = 每個 (性別×年齡帶) cell 至少一張。稀有 statement 的 `none/partial`",
        "> 是 PTB-XL 資料本身的限制，非工具缺陷。",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Build a demographic-spread PTB-XL ECG image bank (§6-A).")
    ap.add_argument("--db", required=True, help="ptbxl_database.csv")
    ap.add_argument("--scp", required=True, help="scp_statements.csv")
    ap.add_argument("--bank-dir", default=os.path.join(ROOT, "image_bank"),
                    help="image bank dir (default repo image_bank/)")
    ap.add_argument("--age-bands", default="0,40,60", help="age-band lower bounds, e.g. 0,40,60")
    ap.add_argument("--per-cell", type=int, default=1, help="images per (sex,age-band) cell")
    ap.add_argument("--min-likelihood", type=float, default=80.0,
                    help="min cardiologist likelihood for abnormal statements")
    ap.add_argument("--sampling", type=int, default=500, choices=[100, 500])
    ap.add_argument("--codes", nargs="*", default=None, help="restrict to these SCP codes")
    ap.add_argument("--source-dir", default=None, help="local PTB-XL root; omit to stream from PhysioNet")
    ap.add_argument("--unverified", action="store_true",
                    help="write verified:false (hidden until you flip) instead of verified:true")
    ap.add_argument("--list-only", action="store_true", help="select + report only, render nothing")
    ap.add_argument("--coverage-out", default=None, help="coverage report path (default <bank>/ptbxl_coverage.md)")
    args = ap.parse_args()

    pd = rp._need("pandas", "pip install pandas")
    for p in (args.db, args.scp):
        if not os.path.isfile(p):
            sys.exit(f"Not found: {p}")
    df = pd.read_csv(args.db, dtype=str)
    meta_all = load_statement_meta(pd.read_csv(args.scp))
    bounds = parse_bands(args.age_bands)
    codes = args.codes or list(meta_all.keys())
    verified = not args.unverified

    bank_dir = args.bank_dir
    os.makedirs(os.path.join(bank_dir, "ecg"), exist_ok=True)
    manifest = load_manifest(bank_dir)
    existing_ids = {e.get("id") for e in manifest["entries"]}

    print(f"PTB-XL: {len(df)} records · {len(codes)} statements · "
          f"bands {bounds} · per-cell {args.per_cell} · "
          f"{'STREAM' if not args.source_dir else 'local'}")
    print("indexing records...")
    index = build_index(df)
    report_rows, rendered, skipped = [], 0, 0
    for code in codes:
        meta = meta_all.get(code)
        if not meta:
            print(f"  ? unknown code {code}, skipping")
            continue
        sel, info = select_for_code(index.get(code, []), code, meta,
                                    args.per_cell, bounds, args.min_likelihood)
        status = coverage_status(info)
        report_rows.append({"code": code, "meta": meta, "info": info, "status": status})
        print(f"  [{code:7}] avail={info['n_available']:4} cells={info['cells_available']}/{info['cells_total']} "
              f"selected={info['n_selected']} ({status})")
        if args.list_only:
            continue
        for s in sel:
            entry = make_entry(code, meta, s, verified)
            if entry["id"] in existing_ids:
                skipped += 1
                continue
            out_path = os.path.join(bank_dir, entry["file"])
            try:
                rec = rp.load_record(s["rec"]["row"], args.sampling, args.source_dir)
                if rec is None:
                    print(f"    ! {entry['id']}: no waveform"); continue
                rp.render(rec, out_path, f"PTB-XL #{entry['ecg_id']} — {code} ({meta['description']})")
            except Exception as e:
                print(f"    ! {entry['id']}: {e}"); continue
            manifest["entries"].append(entry)
            existing_ids.add(entry["id"])
            rendered += 1

    if rendered and not args.list_only:
        save_manifest(bank_dir, manifest)
    cov_path = args.coverage_out or os.path.join(bank_dir, "ptbxl_coverage.md")
    write_coverage_report(cov_path, report_rows, bounds)
    print(f"\nRendered {rendered} new image(s), skipped {skipped} existing. "
          f"Manifest now has {len(manifest['entries'])} entries.")
    print(f"Coverage report: {cov_path}")
    if rendered and verified:
        print("NOTE: entries written verified:true (cardiologist-annotated + human-validated). "
              "Spot-check image_bank/ecg/*.png; the abnormal gate still requires the finding "
              "to be evidenced in the exam report before an image is shown.")


if __name__ == "__main__":
    main()
