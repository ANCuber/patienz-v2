#!/usr/bin/env python3
"""Render a small, curated set of PTB-XL 12-lead ECGs to PNG for the §6-A image
bank (real, de-identified, cardiologist-annotated read-practice films).

PTB-XL (PhysioNet, CC BY 4.0 — Open Access, no credentialing) ships ECG
*waveforms* (WFDB), not images, so this tool:

  1. reads ``ptbxl_database.csv`` (the label metadata),
  2. picks the highest-label-quality records for each requested class
     (prefers human-validated folds 9/10 and high cardiologist likelihood),
  3. renders each as a clinical 3x4 + rhythm-strip 12-lead ECG on ECG graph
     paper (25 mm/s, 10 mm/mV), and
  4. prints the exact ``tools/ingest_local_images.py`` command per class.

It reads waveforms locally if you already downloaded the dataset, otherwise it
**streams only the selected records** from PhysioNet (``pn_dir``) — so you do NOT
need the full 1.7 GB download, just the ~6 MB ``ptbxl_database.csv``.

Requires: ``pip install wfdb matplotlib pandas numpy``.

Quick start (streaming — smallest footprint):
    # 1. get just the label CSV (no waveforms needed up front)
    #    (open access, no login)
    curl -L -o ptbxl_database.csv \\
        https://physionet.org/files/ptb-xl/1.0.3/ptbxl_database.csv
    # 2. render 4 of each class straight from PhysioNet
    python tools/render_ptbxl_ecg.py --db ptbxl_database.csv \\
        --out ./ptbxl_png --classes NORM AFIB MI --per-class 4
    # 3. eyeball ./ptbxl_png/<CLASS>/*.png, then run the printed ingest commands

If you already have the dataset unzipped, pass ``--source-dir /path/to/ptb-xl``
to read locally instead of streaming.

NOTE: PTB-XL labels are cardiologist-assigned and reliable, but you are the
final reviewer — glance at each rendered PNG before running the ingest
command (that eyeball is the "human verified" step the bank requires).
"""
import argparse
import ast
import os
import sys

PN_BASE = "ptb-xl/1.0.3"          # PhysioNet project path for wfdb pn_dir
SOURCE_URL = "https://physionet.org/content/ptb-xl/1.0.3/"

# What each class means to the image bank. ``findings`` are the film's own
# ground-truth (must be evidenced in an exam's report to be shown); the abnormal
# gate keys off these, not the case's headline disease.
CLASS_META = {
    "NORM": {
        "normality": "normal",
        "findings": ["normal sinus rhythm", "normal ECG"],
        "disease_keywords": [],
        "caption": "12-lead ECG — normal sinus rhythm",
        # PTB-XL scp_code(s) that select this class:
        "codes": {"NORM"},
    },
    "AFIB": {
        "normality": "abnormal",
        "findings": ["atrial fibrillation"],
        "disease_keywords": ["atrial fibrillation"],
        "caption": "12-lead ECG — atrial fibrillation",
        "codes": {"AFIB"},
    },
    "MI": {
        "normality": "abnormal",
        "findings": ["myocardial infarction", "ST-T changes"],
        "disease_keywords": ["myocardial infarction", "acute coronary syndrome"],
        "caption": "12-lead ECG — myocardial infarction",
        # Diagnostic MI subclasses in PTB-XL (superclass MI):
        "codes": {"IMI", "AMI", "ASMI", "ALMI", "ILMI", "IPMI", "IPLMI", "LMI",
                  "PMI", "INJAS", "INJAL", "INJIN", "INJLA", "INJIL"},
    },
}

# Clinical 3x4 layout: row -> the lead shown in each of the 4 time-columns.
GRID = [
    ["I", "AVR", "V1", "V4"],
    ["II", "AVL", "V2", "V5"],
    ["III", "AVF", "V3", "V6"],
]
RHYTHM_LEAD = "II"


def _need(mod, hint):
    try:
        return __import__(mod)
    except ImportError:
        sys.exit(f"This tool needs '{mod}' ({hint}).")


def parse_scp(cell):
    """ptbxl_database.csv scp_codes is a stringified dict {code: likelihood}."""
    try:
        d = ast.literal_eval(cell) if isinstance(cell, str) else {}
        return d if isinstance(d, dict) else {}
    except (ValueError, SyntaxError):
        return {}


def select_records(df, cls, per_class, min_likelihood):
    """Pick the best per_class records for a class, best label-quality first."""
    codes = CLASS_META[cls]["codes"]
    rows = []
    for _, r in df.iterrows():
        scp = parse_scp(r.get("scp_codes", ""))
        hits = [scp[c] for c in codes if c in scp]
        if not hits:
            continue
        best = max(hits)
        # NORM likelihood is often 100 or 0(=unknown); accept unknown for NORM
        # only, require a confident label for the abnormal classes.
        if cls == "NORM":
            if not (best >= min_likelihood or best == 0):
                continue
            # a clean normal should not also carry a strong abnormal statement
            if len([v for k, v in scp.items() if k != "NORM" and v >= 50]):
                continue
        else:
            if best < min_likelihood:
                continue
        human = str(r.get("validated_by_human", "")).strip().lower() in ("true", "1")
        try:
            fold = int(r.get("strat_fold", 0) or 0)
        except (TypeError, ValueError):
            fold = 0
        try:
            noise = float(r.get("static_noise", 0) or 0)
        except (TypeError, ValueError):
            noise = 0.0
        rows.append((
            0 if human else 1,          # human-validated first
            0 if fold in (9, 10) else 1,  # high-quality folds first
            -best,                       # higher likelihood first
            noise,                       # less noisy first
            r,
        ))
    rows.sort(key=lambda t: t[:4])
    return [t[4] for t in rows[:per_class]]


def load_record(row, sampling, source_dir):
    wfdb = _need("wfdb", "pip install wfdb")
    col = "filename_hr" if sampling == 500 else "filename_lr"
    rel = str(row.get(col, "")).strip()
    if not rel:
        return None
    base = os.path.basename(rel)          # e.g. 00001_hr
    subdir = os.path.dirname(rel)         # e.g. records500/00000
    if source_dir:
        local = os.path.join(source_dir, subdir, base)
        if os.path.isfile(local + ".hea"):
            return wfdb.rdrecord(local)
    # stream just this record from PhysioNet
    return wfdb.rdrecord(base, pn_dir=f"{PN_BASE}/{subdir}")


def _draw_grid(ax, width_mm, height_mm):
    import numpy as np
    # minor 1 mm (light), major 5 mm (darker) — standard ECG paper.
    for x in np.arange(0, width_mm + 1, 1):
        ax.axvline(x, color="#f4c6c6", lw=0.3, zorder=0)
    for y in np.arange(0, height_mm + 1, 1):
        ax.axhline(y, color="#f4c6c6", lw=0.3, zorder=0)
    for x in np.arange(0, width_mm + 1, 5):
        ax.axvline(x, color="#e88f8f", lw=0.6, zorder=0)
    for y in np.arange(0, height_mm + 1, 5):
        ax.axhline(y, color="#e88f8f", lw=0.6, zorder=0)


def render(record, out_path, title):
    _need("matplotlib", "pip install matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fs = int(record.fs)
    sig = np.asarray(record.p_signal, dtype=float)  # (samples, n_leads), mV
    names = [str(n).upper().strip() for n in record.sig_name]
    idx = {n: i for i, n in enumerate(names)}

    seg = 2.5                       # seconds per column
    mm_per_s, mm_per_mV = 25.0, 10.0
    width_mm = 4 * seg * mm_per_s   # 250 mm
    baselines = [150.0, 110.0, 70.0]
    rhythm_base = 28.0
    height_mm = 170.0

    fig = plt.figure(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=150)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.94])
    ax.set_xlim(0, width_mm)
    ax.set_ylim(0, height_mm)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    _draw_grid(ax, width_mm, height_mm)

    def plot_lead(lead, t0, t1, x0, base):
        i = idx.get(lead)
        if i is None:
            return
        a, b = int(t0 * fs), int(t1 * fs)
        y = sig[a:b, i]
        x = x0 + (np.arange(len(y)) / fs) * mm_per_s
        ax.plot(x, base + y * mm_per_mV, color="#101010", lw=0.7, zorder=2)
        ax.text(x0 + 1, base + 12, lead, fontsize=7, color="#101010",
                ha="left", va="bottom", zorder=3)

    for col in range(4):
        t0, t1 = col * seg, (col + 1) * seg
        x0 = col * seg * mm_per_s
        for row in range(3):
            plot_lead(GRID[row][col], t0, t1, x0, baselines[row])

    # 10 s rhythm strip (lead II)
    if RHYTHM_LEAD in idx:
        i = idx[RHYTHM_LEAD]
        n = min(len(sig), int(10 * fs))
        x = (np.arange(n) / fs) * mm_per_s
        ax.plot(x, rhythm_base + sig[:n, i] * mm_per_mV, color="#101010", lw=0.7, zorder=2)
        ax.text(1, rhythm_base + 12, f"{RHYTHM_LEAD} (rhythm)", fontsize=7,
                color="#101010", ha="left", va="bottom", zorder=3)

    fig.suptitle(f"{title}    ·    25 mm/s · 10 mm/mV", fontsize=9, y=0.99)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)


def ingest_command(cls, folder):
    m = CLASS_META[cls]
    parts = [
        "python tools/ingest_local_images.py", f'"{folder}"',
        "--modality ECG", f"--normality {m['normality']}",
        "--findings " + " ".join(f'"{f}"' for f in m["findings"]),
    ]
    if m["disease_keywords"]:
        parts.append("--disease-keywords " + " ".join(f'"{k}"' for k in m["disease_keywords"]))
    parts += [
        '--source "PTB-XL (PhysioNet)"',
        f'--source-url {SOURCE_URL}',
        '--license "CC BY 4.0"',
        '--attribution "Wagner et al., PTB-XL, PhysioNet"',
        f'--caption "{m["caption"]}"',
        "--verified",
    ]
    return " \\\n    ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Render curated PTB-XL ECGs to PNG for the §6-A image bank.")
    ap.add_argument("--db", required=True, help="path to ptbxl_database.csv")
    ap.add_argument("--out", default="./ptbxl_png", help="output root (one subfolder per class)")
    ap.add_argument("--classes", nargs="+", default=["NORM", "AFIB", "MI"],
                    choices=sorted(CLASS_META), help="which classes to render")
    ap.add_argument("--per-class", type=int, default=4, help="images per class")
    ap.add_argument("--sampling", type=int, default=500, choices=[100, 500],
                    help="use records500 (crisper) or records100")
    ap.add_argument("--min-likelihood", type=float, default=80.0,
                    help="min cardiologist likelihood for abnormal labels")
    ap.add_argument("--source-dir", default=None,
                    help="local PTB-XL root (contains records500/); omit to stream from PhysioNet")
    ap.add_argument("--list-only", action="store_true", help="select + print, render nothing")
    args = ap.parse_args()

    pd = _need("pandas", "pip install pandas")
    if not os.path.isfile(args.db):
        sys.exit(f"Not found: {args.db} (download ptbxl_database.csv first, see --help)")
    df = pd.read_csv(args.db, dtype=str)

    print(f"Loaded {len(df)} PTB-XL records from {args.db}")
    rendered = {}
    for cls in args.classes:
        picks = select_records(df, cls, args.per_class, args.min_likelihood)
        print(f"\n[{cls}] selected {len(picks)} record(s): "
              + ", ".join(str(r.get('ecg_id', '?')) for r in picks))
        if args.list_only or not picks:
            continue
        folder = os.path.join(args.out, cls)
        ok = 0
        for r in picks:
            ecg_id = str(r.get("ecg_id", "rec"))
            out_path = os.path.join(folder, f"ptbxl_{cls}_{ecg_id}.png")
            try:
                rec = load_record(r, args.sampling, args.source_dir)
                if rec is None:
                    print(f"  ! {ecg_id}: no waveform path")
                    continue
                render(rec, out_path, f"PTB-XL #{ecg_id} — {cls}")
                ok += 1
                print(f"  ✓ {out_path}")
            except Exception as e:
                print(f"  ! {ecg_id}: {e}")
        if ok:
            rendered[cls] = folder

    if rendered:
        print("\n=== Next: eyeball the PNGs, then register them (already flagged --verified) ===")
        for cls, folder in rendered.items():
            print(f"\n# {cls}\n{ingest_command(cls, folder)}")
    elif not args.list_only:
        print("\nNothing rendered. Check network/--source-dir, or try --list-only first.")


if __name__ == "__main__":
    main()
