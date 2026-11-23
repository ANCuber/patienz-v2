#!/usr/bin/env python3
"""Clinician review workflow for the image bank: flip ``verified`` and record
the attributes a dataset label does not carry.

Two things about the bank need a human, and both are edits to
``image_bank/manifest.json`` rather than to any image:

1. **verified** — NIH ChestX-ray14's labels are NLP-mined (~90% accurate), so
   only radiologist-boxed films are auto-verified; the rest sit hidden until
   someone looks at them.
2. **laterality** — no open chest-radiograph dataset records which side a
   finding is on, so a report reading 「右側氣胸」 could otherwise be paired with
   a left-sided film. ``util.image_bank`` rejects a film whose recorded side
   contradicts the report, but only once the side has been recorded here.

Round trip (PowerShell, from the repo root):

    # 1. produce the worklist
    python tools/annotate_image_bank.py --export-csv review.csv
    # 2. open review.csv, look at each image, fill in `verified` and `laterality`
    # 3. write the decisions back
    python tools/annotate_image_bank.py --apply-csv review.csv

``--backfill`` upgrades a manifest written by an older builder in place (splits
the packed ``findings`` list and computes ``attributes``) so the new retrieval
gate has structured values to work with without rebuilding the bank.
"""
import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_common import load_manifest, save_manifest  # noqa: E402
from util import image_bank as ib  # noqa: E402

FIELDS = ["id", "modality", "findings", "verified", "laterality", "label_verification",
          "file", "note"]
LATERALITY = ("", "right", "left", "bilateral", "none")
BOOLS = {"true": True, "1": True, "yes": True, "y": True, "t": True,
         "false": False, "0": False, "no": False, "n": False, "f": False}

# Findings whose teaching value depends on the side being right; a film of one
# of these with no recorded side is what --export-csv puts in front of a
# reviewer first.
SIDED_FINDINGS = ("pneumothorax", "pleural effusion", "atelectasis", "consolidation",
                  "pneumonia", "pulmonary mass", "pulmonary nodule", "pulmonary infiltrate",
                  "pleural thickening", "diaphragmatic hernia")


def is_sided(entry):
    blob = " ".join(entry.get("findings") or []).lower()
    return any(f in blob for f in SIDED_FINDINGS)


def attrs_of(entry):
    a = entry.get("attributes")
    return dict(a) if isinstance(a, dict) else None


def needs_review(entry):
    """Why this entry is on the worklist, or None when it is finished.

    ``verified`` only decides whether a film may be shown. A film published with
    ``--publish`` is shown while nobody here has checked its label, so it stays
    on the worklist -- a radiologist's bounding box localizes a finding, it does
    not adjudicate the label it was drawn for.
    """
    if entry.get("review_verdict") == "rejected":
        return None                     # a reviewer looked and said no
    if not entry.get("verified"):
        return "unverified"
    if ib.review_status(entry) in ("nlp-mined", "radiologist-bbox", "unknown"):
        return "label-unreviewed"
    a = attrs_of(entry) or {}
    if is_sided(entry) and not (a.get("laterality") or a.get("laterality_reviewed")):
        return "laterality-unknown"
    return None


def backfill(entries):
    """Give a legacy entry the fields the retrieval gate reads, without a rebuild.

    Older manifests packed the film's own statement and its umbrella terms into
    one ``findings`` list; the first item was always the statement.
    """
    changed = 0
    for e in entries:
        touched = False
        if "umbrella_terms" not in e:
            f = list(e.get("findings") or [])
            e["findings"], e["umbrella_terms"] = f[:1], f[1:]
            touched = True
        if not isinstance(e.get("attributes"), dict):
            e["attributes"] = ib.claim_attributes(*ib.claim_terms(e))
            touched = True
        if not e.get("label_verification") and str(e.get("source", "")).startswith("PTB-XL"):
            # written by newer builds; added here so an existing bank stops
            # reporting "label provenance not recorded" for every ECG.
            e["label_verification"] = "cardiologist-validated"
            touched = True
        changed += 1 if touched else 0
    return changed


def recompute_attributes(entries):
    """Recompute the derived attributes from each entry's own finding.

    Needed whenever the attribute vocabulary in ``util.image_bank`` changes:
    the stored values were computed by the old vocabulary and the gate prefers
    stored over derived, so a fix would otherwise not reach an existing bank.
    A reviewer's decisions (``laterality`` and its reviewed flag) are the one
    thing that is *not* derived, so they are carried across untouched.
    """
    changed = 0
    for e in entries:
        old = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
        # the full claim: statement + validated co-findings (same helper the
        # enrichment tool and the retrieval gate use)
        fresh = ib.claim_attributes(*ib.claim_terms(e))
        for k in ("laterality", "laterality_reviewed", "laterality_source"):
            if old.get(k) is not None:
                fresh[k] = old[k]
        if fresh != old:
            e["attributes"] = fresh
            changed += 1
    return changed


def publish(entries, modalities):
    """Show every film of these modalities, reviewed or not.

    This is a deliberate trade: it puts un-adjudicated dataset labels in front of
    students, and the page then carries the matching warning (red for text-mined
    labels, amber for a radiologist bounding box). It does NOT mark anything as
    clinician-reviewed -- the films stay on the worklist until a person actually
    looks at them.

    A film a reviewer *rejected* (``review_verdict == "rejected"``) is skipped:
    an unverified entry and a rejected one look alike in ``verified``, and
    re-publishing the rejection would overwrite the one decision this tool must
    never touch.
    """
    wanted = {m.upper() for m in modalities}
    changed = skipped = 0
    for e in entries:
        if e.get("modality") not in wanted or e.get("verified"):
            continue
        if e.get("review_verdict") == "rejected":
            skipped += 1
            continue
        e["verified"] = True
        e["published_without_review"] = True
        changed += 1
    if skipped:
        print("  kept hidden: %d film(s) a reviewer rejected" % skipped)
    return changed


def export_csv(entries, path, include_done):
    rows = []
    for e in entries:
        why = needs_review(e)
        if why is None and not include_done:
            continue
        rows.append({
            "id": e.get("id", ""),
            "modality": e.get("modality", ""),
            "findings": "; ".join(e.get("findings") or []),
            "verified": "true" if e.get("verified") else "false",
            "laterality": (attrs_of(e) or {}).get("laterality") or "",
            "label_verification": e.get("label_verification", ""),
            "file": e.get("file", ""),
            "note": why or "",
        })
    # gaps first: unverified films block the most, laterality next
    order = {"unverified": 0, "label-unreviewed": 1, "laterality-unknown": 2, "": 3}
    rows.sort(key=lambda r: (order.get(r["note"], 3), r["modality"], r["id"]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def apply_csv(entries, path):
    by_id = {e.get("id"): e for e in entries}
    seen = updated = 0
    problems = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ident = (row.get("id") or "").strip()
            if not ident:
                continue
            seen += 1
            e = by_id.get(ident)
            if e is None:
                problems.append("unknown id: %s" % ident)
                continue
            if apply_row(e, row, problems):
                updated += 1
    return seen, updated, problems


def apply_row(entry, row, problems):
    touched = False
    raw = (row.get("verified") or "").strip().lower()
    if raw:
        if raw not in BOOLS:
            problems.append("%s: bad verified value %r" % (entry.get("id"), raw))
        else:
            want = BOOLS[raw]
            if bool(entry.get("verified")) != want:
                entry["verified"] = want
                touched = True
            # Someone opened the file and made a call. Two different things get
            # recorded: ``clinician_reviewed`` means a clinician *vouches for the
            # label* (only ever set by an acceptance -- review_status() reports
            # it on screen), while ``review_verdict`` records that a decision was
            # made at all. A rejection needs the second without the first, or it
            # is indistinguishable from "not looked at yet" and --publish puts
            # the film back in front of students.
            verdict = "accepted" if want else "rejected"
            if want and not entry.get("clinician_reviewed"):
                entry["clinician_reviewed"] = True
                touched = True
            if entry.get("review_verdict") != verdict:
                entry["review_verdict"] = verdict
                touched = True
            if want:
                entry.pop("published_without_review", None)
    side = (row.get("laterality") or "").strip().lower()
    if side:
        if side not in LATERALITY:
            problems.append("%s: bad laterality %r (use %s)"
                            % (entry.get("id"), side, "/".join(x for x in LATERALITY if x)))
        else:
            if not isinstance(entry.get("attributes"), dict):
                entry["attributes"] = ib.claim_attributes(*(entry.get("findings") or []))
            # "none" records a reviewed decision that the finding has no side;
            # "" means still unreviewed. The two must stay distinguishable, so
            # the reviewed flag is stamped even when the value does not change --
            # otherwise a "no side here" verdict is silently dropped and the film
            # sits on the worklist forever.
            attrs = entry["attributes"]
            value = None if side == "none" else side
            if attrs.get("laterality") != value or not attrs.get("laterality_reviewed"):
                attrs["laterality"] = value
                attrs["laterality_reviewed"] = True
                touched = True
    note = (row.get("note") or "").strip()
    if note and note not in ("unverified", "label-unreviewed", "laterality-unknown"):
        if entry.get("review_note") != note:
            entry["review_note"] = note
            touched = True
    return touched


def status(entries):
    from collections import Counter
    print("entries: %d" % len(entries))
    for mod in sorted({e.get("modality") for e in entries if e.get("modality")}):
        rows = [e for e in entries if e.get("modality") == mod]
        why = Counter(needs_review(e) for e in rows)
        # laterality is counted on its own: needs_review() reports only the
        # highest-priority reason, so a film waiting on a label review would
        # otherwise hide the fact that its side is unrecorded too.
        no_side = sum(1 for e in rows if is_sided(e)
                      and not ((attrs_of(e) or {}).get("laterality")
                               or (attrs_of(e) or {}).get("laterality_reviewed")))
        print("  %-4s %4d total | %4d 顯示中 | %3d 隱藏 | %3d 標籤未經醫師覆核 | %3d 側性未標註"
              % (mod, len(rows), sum(1 for e in rows if e.get("verified")),
                 why.get("unverified", 0), why.get("label-unreviewed", 0), no_side))
    legacy = sum(1 for e in entries if "umbrella_terms" not in e
                 or not isinstance(e.get("attributes"), dict))
    print("  entries still in the pre-attribute format: %d%s"
          % (legacy, "  (run --backfill)" if legacy else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bank-dir", default=None, help="default: image_bank/ (or $PATIENZ_IMAGE_BANK_DIR)")
    ap.add_argument("--status", action="store_true", help="what still needs a human")
    ap.add_argument("--export-csv", metavar="PATH", help="write the review worklist")
    ap.add_argument("--all", action="store_true", help="with --export-csv: include finished entries too")
    ap.add_argument("--apply-csv", metavar="PATH", help="read reviewer decisions back in")
    ap.add_argument("--backfill", action="store_true",
                    help="add umbrella_terms/attributes to a manifest from an older builder")
    ap.add_argument("--publish", nargs="+", metavar="MODALITY",
                    help="show every film of these modalities even though nobody here has "
                         "reviewed the labels; the page carries the un-reviewed warning")
    ap.add_argument("--recompute-attributes", action="store_true",
                    help="re-derive territory/completeness/lesion after a vocabulary change "
                         "(reviewer-set laterality is preserved)")
    ap.add_argument("--set", metavar="ID", help="edit one entry directly")
    ap.add_argument("--laterality", choices=[x for x in LATERALITY if x], help="with --set")
    ap.add_argument("--verified", choices=["true", "false"], help="with --set")
    ap.add_argument("--dry-run", action="store_true", help="report changes but do not write")
    args = ap.parse_args()

    directory = args.bank_dir or ib.bank_dir()
    man = load_manifest(directory)
    entries = man["entries"]
    if not entries:
        print("no manifest entries under %s" % directory)
        return 1

    dirty = False
    if args.backfill:
        n = backfill(entries)
        print("backfilled %d entr(ies)" % n)
        dirty = dirty or bool(n)
    if args.publish:
        n = publish(entries, args.publish)
        print("published %d previously hidden film(s): %s" % (n, ", ".join(args.publish)))
        print("  they now display WITH the un-reviewed-label warning, and stay on the worklist")
        dirty = dirty or bool(n)
    if args.recompute_attributes:
        n = recompute_attributes(entries)
        print("recomputed attributes on %d entr(ies)" % n)
        dirty = dirty or bool(n)
    if args.export_csv:
        n = export_csv(entries, args.export_csv, args.all)
        print("wrote %d row(s) to %s" % (n, args.export_csv))
    if args.apply_csv:
        seen, updated, problems = apply_csv(entries, args.apply_csv)
        print("read %d row(s), updated %d entr(ies)" % (seen, updated))
        for p in problems:
            print("  ! %s" % p)
        dirty = dirty or bool(updated)
        if problems and not updated:
            return 1
    if args.set:
        target = next((e for e in entries if e.get("id") == args.set), None)
        if target is None:
            print("no entry with id %s" % args.set)
            return 1
        problems = []
        touched = apply_row(target, {"verified": args.verified or "",
                                     "laterality": args.laterality or ""}, problems)
        for p in problems:
            print("  ! %s" % p)
        print("%s: %s" % (args.set, "updated" if touched else "no change"))
        dirty = dirty or touched
    if args.status or not (args.export_csv or args.apply_csv or args.set or args.backfill
                           or args.recompute_attributes or args.publish):
        status(entries)

    if dirty and not args.dry_run:
        save_manifest(directory, man)
        print("manifest saved: %s" % os.path.join(directory, "manifest.json"))
    elif dirty:
        print("--dry-run: manifest NOT written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
