#!/usr/bin/env python3
"""Re-render every ECG film in the bank with a neutral title.

The first build captioned each film with its SCP statement — "PTB-XL #4166 — IMI
(inferior myocardial infarction)" — which put the answer on the image students
are asked to read. This re-renders the same records to the same paths with only
the record id and the paper scale in the title. The manifest is not touched:
ids, files and annotations stay exactly as they are.

Waveforms are streamed from PhysioNet per record (or read from --source-dir).

Usage (PowerShell, repo root):
  python tools/rerender_ecg_images.py --db ptbxl_database.csv
  python tools/rerender_ecg_images.py --db ptbxl_database.csv --only ptbxl-IMI-4166 ptbxl-ASMI-1440
"""
import argparse
import csv
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_ptbxl_ecg as rp  # noqa: E402
from bank_common import load_manifest  # noqa: E402
from util import image_bank as ib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(ROOT, "ptbxl_database.csv"))
    ap.add_argument("--bank-dir", default=None)
    ap.add_argument("--source-dir", default=None, help="local PTB-XL copy (else stream from PhysioNet)")
    ap.add_argument("--sampling", type=int, default=500, choices=(100, 500))
    ap.add_argument("--only", nargs="*", default=None, help="manifest ids to re-render (default: all ECG)")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    bank = args.bank_dir or ib.bank_dir()
    man = load_manifest(bank)
    rows = {r["ecg_id"]: r for r in csv.DictReader(io.open(args.db, encoding="utf-8"))}
    targets = [e for e in man["entries"] if e.get("modality") == "ECG"
               and (not args.only or e.get("id") in set(args.only))]
    print("re-rendering %d ECG film(s) with neutral titles" % len(targets))
    done = failed = 0
    for n, e in enumerate(targets, 1):
        row = rows.get(str(e.get("ecg_id")))
        out = os.path.join(bank, e["file"].replace("/", os.sep))
        if row is None:
            print("  ! %s: ecg_id %s not in %s" % (e["id"], e.get("ecg_id"), args.db))
            failed += 1
            continue
        for attempt in range(1, args.retries + 1):
            try:
                rec = rp.load_record(row, args.sampling, args.source_dir)
                if rec is None:
                    raise RuntimeError("no waveform filename in the database row")
                tmp = out + ".tmp.png"
                rp.render(rec, tmp, rp.neutral_title(e["ecg_id"]))
                os.replace(tmp, out)
                done += 1
                break
            except Exception as exc:  # noqa: BLE001 - report and retry
                if attempt == args.retries:
                    print("  ! %s: %s" % (e["id"], exc))
                    failed += 1
                else:
                    time.sleep(2 * attempt)
        if n % 25 == 0 or n == len(targets):
            print("  %d/%d (%d ok, %d failed)" % (n, len(targets), done, failed))
    print("done: %d re-rendered, %d failed" % (done, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
