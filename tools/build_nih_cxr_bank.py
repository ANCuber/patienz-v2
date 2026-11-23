#!/usr/bin/env python3
"""Build a demographic-spread NIH ChestX-ray14 chest-radiograph image bank (§6-A).

Same shape as ``build_ptbxl_bank.py``, one modality over: for each of the 14 NIH
findings (plus the clean "No Finding" normal), pick the best real, de-identified
frontal chest film in **each sex x age-band cell**, copy it into the bank, write
manifest entries, and emit an honest coverage report.

WHY THE VERIFICATION DEFAULT IS INVERTED VS. THE ECG BUILDER
------------------------------------------------------------
PTB-XL's statements are cardiologist annotations with an explicit likelihood and
a ``validated_by_human`` flag, so ``build_ptbxl_bank.py`` writes ``verified:true``.
ChestX-ray14's 14 labels are **NLP-mined from the free-text radiology reports**
and the authors state ~90% accuracy (Wang et al., ChestX-ray8, CVPR 2017); the
independent audit by Oakden-Rayner (*Exploring Large-scale Public Medical Image
Datasets*, Acad Radiol 2020) found substantial label noise, worst for Pneumonia,
Infiltration and Consolidation. A 1-in-10 wrong label is not acceptable on an
image a student is asked to *read*, so by default only the films whose finding a
board-certified radiologist actually drew a box on (``BBox_List_2017.csv``: 984
boxes / 880 images / 8 of the 14 findings) are written ``verified:true``.
Everything else lands ``verified:false`` — present in the manifest, invisible to
retrieval — until a clinician reviews it and flips the flag. ``--verify all``
opts into trusting the NLP labels; ``--verify none`` marks everything for review.

Data (open access, NIH Clinical Center — no login, but no per-file URL, so the
images must be downloaded and extracted locally first):
    https://nihcc.app.box.com/v/ChestXray-NIHCC
      Data_Entry_2017.csv   metadata for all 112,120 images / 30,805 patients
      BBox_List_2017.csv    radiologist-drawn boxes (the verification source)
      images_001.tar.gz ... images_012.tar.gz   (~42 GB total, extract anywhere)

Typical runs:
    # 1) see what the data can cover, no images needed
    python tools/build_nih_cxr_bank.py --meta Data_Entry_2017.csv \
        --bbox BBox_List_2017.csv --list-only
    # 2) actually build (after extracting the image tarballs)
    python tools/build_nih_cxr_bank.py --meta Data_Entry_2017.csv \
        --bbox BBox_List_2017.csv --images-dir C:/nih-cxr

Stdlib-only for the metadata pass (no pandas): 112k rows is nothing for ``csv``,
and it keeps the selection logic unit-testable with zero dependencies. Pillow is
imported lazily and only when ``--max-px`` asks for a downscale.
"""
import argparse
import csv
import os
import shutil
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bank_common import (age_band, band_labels, cells_total, coverage_status,  # noqa: E402
                         load_manifest, norm_sex, parse_bands, save_manifest,
                         summarize, write_report)
# The attribute vocabulary (territory / laterality / lesion) lives with the
# retrieval that enforces it, so a builder can never disagree with the gate.
from util import image_bank as ib  # noqa: E402

SOURCE = "NIH ChestX-ray14 (NIH Clinical Center)"
SOURCE_URL = "https://nihcc.app.box.com/v/ChestXray-NIHCC"
LICENSE = "NIH Clinical Center — open access for research/education, no usage restrictions stated"
ATTRIBUTION = "Wang et al., ChestX-ray8, CVPR 2017 — NIH Clinical Center"

NO_FINDING = "No Finding"

# NIH label -> (canonical clinical phrase used as the entry's finding, zh label
# for the coverage report). The phrase is what ``util.image_bank`` looks up via
# ``grading_normalize.surface_forms`` inside the examiner's report, so it must be
# a term that has a synonym group there — the bare NIH label often is not
# ("Effusion" alone would also fire on a *pericardial* effusion).
LABELS = {
    "Atelectasis":        ("atelectasis",          "肺不張"),
    "Cardiomegaly":       ("cardiomegaly",         "心臟肥大"),
    "Consolidation":      ("consolidation",        "肺實變"),
    "Edema":              ("pulmonary edema",      "肺水腫"),
    "Effusion":           ("pleural effusion",     "肋膜積水"),
    "Emphysema":          ("pulmonary emphysema",  "肺氣腫"),
    "Fibrosis":           ("pulmonary fibrosis",   "肺纖維化"),
    "Hernia":             ("diaphragmatic hernia", "橫膈疝"),
    "Infiltration":       ("pulmonary infiltrate", "肺浸潤"),
    "Mass":               ("pulmonary mass",       "肺腫塊"),
    "Nodule":             ("pulmonary nodule",     "肺結節"),
    "Pleural_Thickening": ("pleural thickening",   "肋膜增厚"),
    "Pneumonia":          ("pneumonia",            "肺炎"),
    "Pneumothorax":       ("pneumothorax",         "氣胸"),
    NO_FINDING:           ("no acute cardiopulmonary finding", "無異常"),
}

# BBox_List_2017.csv spells one finding differently from Data_Entry_2017.csv.
BBOX_LABEL_ALIASES = {"Infiltrate": "Infiltration"}

# Findings that are only assessable on a PA (posteroanterior) film. An AP /
# portable film magnifies the cardiac silhouette, so the cardiothoracic ratio
# cannot be judged on it — an "AP cardiomegaly" film would teach a wrong habit.
REQUIRE_PA = {"Cardiomegaly"}

# Data_Entry_2017.csv contains transcription errors in the age column (values
# 148-155 and 411-414). Anything past this is a data error, not a patient.
MAX_PLAUSIBLE_AGE = 120


# ---------- pure parsing ----------
def parse_age(raw):
    """NIH age -> years, or ``None`` when unusable.

    Handles both releases: the 2017 file encodes ``058Y`` / ``003M`` / ``012D``,
    the 2020 revision plain integers. Implausible ages (the known 148-155 and
    411-414 transcription errors) are rejected outright rather than folded into
    the top band — unlike PTB-XL's ``300`` these are mistakes, not a
    de-identification sentinel meaning "very old".
    """
    s = str(raw or "").strip().upper()
    if not s:
        return None
    unit, num = "Y", s
    if s[-1] in ("Y", "M", "W", "D"):
        unit, num = s[-1], s[:-1]
    try:
        v = float(num)
    except ValueError:
        return None
    years = {"Y": v, "M": v / 12.0, "W": v / 52.0, "D": v / 365.0}[unit]
    if years < 0 or years > MAX_PLAUSIBLE_AGE:
        return None
    return years


def parse_labels(raw):
    """``"Cardiomegaly|Effusion"`` -> ``{"Cardiomegaly", "Effusion"}``. Unknown
    label strings are kept so co-finding counts stay honest."""
    return {p.strip() for p in str(raw or "").split("|") if p.strip()}


def label_slug(label):
    return label.lower().replace("_", "-").replace(" ", "-")


def findings_for(label):
    phrase, zh = LABELS.get(label, (label.lower(), label))
    normal = label == NO_FINDING
    return {
        "findings": [phrase],
        "umbrella_terms": [],
        # NIH labels carry no side, so ``laterality`` lands None and a reviewer
        # fills it in (tools/annotate_image_bank.py). Until it is filled the
        # laterality gate simply has nothing to compare, and a right-sided
        # report can still be paired with a left-sided film -- which is why the
        # coverage report flags un-annotated films.
        "attributes": ib.claim_attributes(phrase),
        "disease_keywords": [] if normal else [phrase],
        "normality": "normal" if normal else "abnormal",
        "zh": zh,
        "phrase": phrase,
    }


# ---------- CSV readers ----------
def _to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# The 2017 release names the sex column "Patient Gender"; the corrected 2020
# re-release (Data_Entry_2017_v2020.csv) renamed it to "Patient Sex". Accept
# either -- reading the wrong one drops every record for missing demographics
# and reports honest-looking zeroes for all 15 findings.
SEX_COLUMNS = ("Patient Sex", "Patient Gender")
REQUIRED_COLUMNS = ("Image Index", "Finding Labels", "Follow-up #",
                    "Patient ID", "Patient Age", "View Position")


def read_metadata(path):
    """``Data_Entry_2017.csv`` -> lean record dicts (only what selection needs)."""
    out = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = [(h or "").strip() for h in (reader.fieldnames or [])]
        sex_col = next((c for c in SEX_COLUMNS if c in fields), None)
        missing = [c for c in REQUIRED_COLUMNS if c not in fields]
        if sex_col is None:
            missing.append(" or ".join(SEX_COLUMNS))
        if missing:
            # Fail loudly: a renamed column must never degrade into "0 available".
            raise ValueError("%s is missing required column(s): %s\ncolumns found: %s"
                             % (path, ", ".join(missing), ", ".join(fields)))
        for row in reader:
            out.append({
                "image": str(row.get("Image Index", "")).strip(),
                "labels": parse_labels(row.get("Finding Labels")),
                "follow_up": _to_int(row.get("Follow-up #"), 9999),
                "patient": str(row.get("Patient ID", "")).strip(),
                "age": parse_age(row.get("Patient Age")),
                "sex": norm_sex(row.get(sex_col)),
                "view": str(row.get("View Position", "")).strip().upper(),
            })
    return out


def read_bbox(path):
    """``BBox_List_2017.csv`` -> ``image index -> {finding labels a radiologist
    drew a box for}``. This is the only human-verified signal in the dataset."""
    out = defaultdict(set)
    if not path or not os.path.isfile(path):
        return dict(out)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            img = str(row.get("Image Index", "")).strip()
            lab = str(row.get("Finding Label", "")).strip()
            if img and lab:
                out[img].add(BBOX_LABEL_ALIASES.get(lab, lab))
    return dict(out)


# ---------- selection across sex x age-band cells (pure) ----------
def build_index(recs):
    """Single pass -> ``label -> [rec]`` (the ``No Finding`` bucket included)."""
    index = defaultdict(list)
    for rec in recs:
        for lab in rec["labels"]:
            index[lab].append(rec)
    return index


def select_for_label(recs, label, per_cell, bounds, bbox_map=None):
    """Pick up to ``per_cell`` films per (sex, age-band) cell for one finding.

    Ranking inside a cell, in order: radiologist-boxed first (so a cell never
    yields an unverifiable film when a verified one exists in it), then PA over
    AP, then fewest co-existing findings (a film that shows *one* thing teaches
    it better), then the earliest study of that patient, then image id for
    determinism.

    A patient contributes at most one film per finding, so a label's ten cells
    are ten different people rather than one person's follow-up series.
    """
    bbox_map = bbox_map or {}
    normal = label == NO_FINDING
    bycell = defaultdict(list)
    n_available = n_bbox = 0
    for rec in recs:
        if normal:
            # a clean normal only: "No Finding" must be the *sole* label
            if rec["labels"] != {NO_FINDING}:
                continue
        else:
            if label not in rec["labels"] or NO_FINDING in rec["labels"]:
                continue
            if label in REQUIRE_PA and rec["view"] != "PA":
                continue
        if rec["sex"] is None or rec["age"] is None:
            continue
        band = age_band(rec["age"], bounds)
        if band is None:
            continue
        n_available += 1
        boxed = label in bbox_map.get(rec["image"], ())
        n_bbox += 1 if boxed else 0
        n_co = 0 if normal else len(rec["labels"] - {label})
        qkey = (0 if boxed else 1, 0 if rec["view"] == "PA" else 1,
                n_co, rec["follow_up"], rec["image"])
        bycell[(rec["sex"], band)].append((qkey, rec, boxed))

    selected, used_patients = [], set()
    for cell in sorted(bycell):
        taken = 0
        for _qkey, rec, boxed in sorted(bycell[cell], key=lambda x: x[0]):
            if taken >= per_cell:
                break
            if rec["patient"] and rec["patient"] in used_patients:
                continue
            used_patients.add(rec["patient"])
            selected.append({"rec": rec, "cell": cell, "bbox_verified": boxed})
            taken += 1

    return selected, {
        "n_available": n_available,
        "n_bbox": n_bbox,
        "cells_eligible": len(bycell),
        # cells that actually produced a film — patient de-duplication can empty
        # an otherwise-eligible cell, and calling that "covered" would be a lie.
        "cells_available": len({s["cell"] for s in selected}),
        "cells_total": cells_total(bounds),
        "n_selected": len(selected),
        "n_selected_bbox": sum(1 for s in selected if s["bbox_verified"]),
    }


# ---------- manifest entries ----------
def make_entry(label, sel, verify_mode):
    """One manifest entry.

    ``findings`` deliberately carries ONLY the finding this entry was selected
    for. Retrieval may surface a film solely on the strength of a finding we can
    stand behind; the other NLP labels ride along in ``co_findings`` for
    transparency but can never *cause* the film to be shown.
    """
    rec = sel["rec"]
    fm = findings_for(label)
    stem = os.path.splitext(rec["image"])[0]
    entry_id = "nih-{}-{}".format(label_slug(label), stem)
    if verify_mode == "all":
        verified = True
    elif verify_mode == "none":
        verified = False
    else:                                   # "bbox" (default)
        verified = bool(sel["bbox_verified"])
    view = rec["view"] or "frontal"
    return {
        "id": entry_id,
        "file": os.path.join("cxr", entry_id + ".png").replace("\\", "/"),
        "modality": "CXR",
        "normality": fm["normality"],
        "findings": fm["findings"],
        "umbrella_terms": fm["umbrella_terms"],
        "attributes": fm["attributes"],
        "disease_keywords": fm["disease_keywords"],
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "deidentified": True,
        "synthetic": False,
        "verified": verified,
        "label_verification": "radiologist-bbox" if sel["bbox_verified"] else "nlp-mined",
        "nih_label": label,
        "co_findings": sorted(rec["labels"] - {label, NO_FINDING}),
        "view_position": view,
        "age": int(rec["age"]) if rec["age"] is not None else None,
        "sex": rec["sex"],
        "image_index": rec["image"],
        "patient_id": rec["patient"],
        "caption": "Chest radiograph ({}) — {}".format(view, fm["phrase"]),
    }


# ---------- coverage report ----------
def write_coverage_report(path, rows, bounds, verify_mode):
    lbl = "/".join(band_labels(bounds))
    s = summarize(rows)
    n_boxed = sum(r["info"]["n_selected_bbox"] for r in rows)
    grid = s["n_classes"] * cells_total(bounds)
    pct = 100.0 * s["cells_filled"] / max(1, grid)
    summary_lines = [
        f"目標分層：性別(2) × 年齡帶({lbl}) = {cells_total(bounds)} 個 cell/finding",
        f"有 ≥1 張影像的 finding：**{s['n_any']} / {s['n_classes']}**",
        f"各分層全覆蓋(ok)的 finding：**{s['n_ok']} / {s['n_classes']}**",
        f"總選入影像：**{s['n_images']}** 張（cell 覆蓋率 {s['cells_filled']}/{grid} = {pct:.1f}%）",
        f"其中放射科醫師標註框確認：**{n_boxed}** 張（`--verify {verify_mode}`；"
        f"其餘 {s['n_images'] - n_boxed} 張需人工複核後才會顯示）",
    ]
    table_rows = []
    for r in rows:
        i, f = r["info"], findings_for(r["label"])
        table_rows.append((r["status"], -i["n_available"], [
            f"`{r['label']}`", f["zh"], f["normality"], i["n_available"],
            i["n_selected"], f"{i['cells_available']}/{i['cells_total']}",
            i["n_selected_bbox"], r["status"],
        ]))
    write_report(
        path, "NIH ChestX-ray14 胸部X光影像庫覆蓋報告（§6-A）", summary_lines,
        ["finding", "中文", "正常/異常", "可用影像", "已選", "cell 覆蓋", "醫師標框", "狀態"],
        table_rows,
        ["`none` = 無合格影像；`partial` = 只覆蓋部分分層；`ok` = 每個 (性別×年齡帶) cell 至少一張。",
         "ChestX-ray14 的 14 個標籤是以 NLP 從報告文字探勘而來（作者宣稱約 90% 準確度；",
         "Oakden-Rayner 2020 的人工稽核發現 Pneumonia／Infiltration／Consolidation 雜訊最大），",
         "因此預設只有「醫師標框」欄位計入的影像寫成 `verified:true`；其餘一律 `verified:false`，",
         "在臨床人員逐張複核並手動翻轉前不會被檢索到。這是刻意的保守設定，不是工具缺陷。"])


# ---------- image copying ----------
def index_images(images_dir):
    """filename -> absolute path. The Box archive extracts to images_001/images/…
    through images_012/images/…, so we walk instead of assuming a flat folder."""
    found = {}
    for root, _dirs, files in os.walk(images_dir):
        for fn in files:
            if fn.lower().endswith(".png") and fn not in found:
                found[fn] = os.path.join(root, fn)
    return found


def copy_image(src, dst, max_px=0):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not max_px:
        shutil.copyfile(src, dst)
        return
    try:
        from PIL import Image
    except ImportError:
        sys.exit("--max-px needs Pillow: pip install Pillow")
    with Image.open(src) as im:
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        im.save(dst, format="PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser(
        description="Build a demographic-spread NIH ChestX-ray14 CXR image bank (§6-A).")
    ap.add_argument("--meta", required=True, help="Data_Entry_2017.csv")
    ap.add_argument("--bbox", default=None,
                    help="BBox_List_2017.csv — the radiologist-confirmed subset; without it "
                         "nothing can be auto-verified under the default --verify bbox")
    ap.add_argument("--images-dir", default=None,
                    help="root of the extracted images_0*.tar.gz (walked recursively). "
                         "Required unless --list-only: NIH serves no per-file URL, so there "
                         "is nothing to stream")
    ap.add_argument("--bank-dir", default=os.path.join(ROOT, "image_bank"))
    ap.add_argument("--age-bands", default="0,19,40,60,80",
                    help="age-band lower bounds. Default 0,19,40,60,80 -> 0-18 (胸廓仍在發育，"
                         "心胸比與縱膈影標準和成人不同) / 19-39 (成人基線，異常率最低) / "
                         "40-59 (吸菸與職業暴露累積，結節/腫塊開始出現) / 60-79 (COPD、心衰竭、"
                         "肺炎盛行率高峰) / 80+ (脊柱後凸與肺纖維化改變基礎影像外觀)")
    ap.add_argument("--per-cell", type=int, default=1, help="images per (sex,age-band) cell")
    ap.add_argument("--labels", nargs="*", default=None, help="restrict to these NIH labels")
    ap.add_argument("--verify", choices=["bbox", "all", "none"], default="bbox",
                    help="which entries get verified:true. bbox (default) = only films a "
                         "radiologist drew a box on; all = trust the ~90%%-accurate NLP labels; "
                         "none = everything awaits your review")
    ap.add_argument("--max-px", type=int, default=0,
                    help="downscale the longest edge to this many px (needs Pillow); "
                         "0 = copy the original 1024x1024 untouched")
    ap.add_argument("--list-only", action="store_true", help="select + report only, copy nothing")
    ap.add_argument("--coverage-out", default=None,
                    help="coverage report path (default <bank>/nih_cxr_coverage.md)")
    args = ap.parse_args()

    if not os.path.isfile(args.meta):
        sys.exit(f"Not found: {args.meta}\nDownload it from {SOURCE_URL}")
    if not args.list_only and not args.images_dir:
        sys.exit("--images-dir is required (NIH publishes no per-file URL, so images cannot be "
                 f"streamed). Download and extract images_001..012.tar.gz from {SOURCE_URL}, "
                 "or re-run with --list-only to see coverage from the CSV alone.")
    if args.verify == "bbox" and not args.bbox:
        print("WARNING: no --bbox given, so under --verify bbox every entry lands "
              "verified:false and stays hidden until you review it.")

    bounds = parse_bands(args.age_bands)
    labels = args.labels or list(LABELS.keys())
    recs = read_metadata(args.meta)
    bbox_map = read_bbox(args.bbox)
    index = build_index(recs)
    print(f"ChestX-ray14: {len(recs)} images · {len({r['patient'] for r in recs})} patients · "
          f"{len(bbox_map)} radiologist-boxed · {len(labels)} findings · bands {bounds} · "
          f"per-cell {args.per_cell} · verify={args.verify}")

    files = {}
    if not args.list_only:
        print(f"indexing {args.images_dir} ...")
        files = index_images(args.images_dir)
        print(f"  {len(files)} png files found")

    bank_dir = args.bank_dir
    manifest = load_manifest(bank_dir)
    existing_ids = {e.get("id") for e in manifest["entries"]}
    rows, copied, skipped, missing = [], 0, 0, 0

    for label in labels:
        if label not in LABELS:
            print(f"  ? unknown label {label}, skipping")
            continue
        sel, info = select_for_label(index.get(label, []), label, args.per_cell, bounds, bbox_map)
        status = coverage_status(info)
        rows.append({"label": label, "info": info, "status": status})
        print(f"  [{label:18}] avail={info['n_available']:6} boxed={info['n_bbox']:4} "
              f"cells={info['cells_available']}/{info['cells_total']} "
              f"selected={info['n_selected']} ({status})")
        if args.list_only:
            continue
        for s in sel:
            entry = make_entry(label, s, args.verify)
            if entry["id"] in existing_ids:
                skipped += 1
                continue
            src = files.get(entry["image_index"])
            if not src:
                print(f"    ! {entry['image_index']}: not found under --images-dir")
                missing += 1
                continue
            try:
                copy_image(src, os.path.join(bank_dir, entry["file"]), args.max_px)
            except Exception as e:                    # noqa: BLE001 - report and keep going
                print(f"    ! {entry['id']}: {e}")
                continue
            manifest["entries"].append(entry)
            existing_ids.add(entry["id"])
            copied += 1

    if copied and not args.list_only:
        save_manifest(bank_dir, manifest)
    cov_path = args.coverage_out or os.path.join(bank_dir, "nih_cxr_coverage.md")
    write_coverage_report(cov_path, rows, bounds, args.verify)
    cxr = [e for e in manifest["entries"] if e.get("modality") == "CXR"]
    n_vis = sum(1 for e in cxr if e.get("verified"))
    print(f"\nCopied {copied} new image(s), skipped {skipped} existing, {missing} missing on disk. "
          f"Manifest now has {len(manifest['entries'])} entries.")
    print(f"CXR entries: {n_vis} verified (retrievable) / {len(cxr) - n_vis} awaiting review (hidden).")
    print(f"Coverage report: {cov_path}")
    if len(cxr) - n_vis:
        print('Review the hidden films in image_bank/cxr/, then flip their "verified" to true in '
              "image_bank/manifest.json. Until then they are inert — the retrieval gate in "
              "util/image_bank.py drops every entry with verified:false.")


if __name__ == "__main__":
    main()
