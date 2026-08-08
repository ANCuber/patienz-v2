#!/usr/bin/env python3
"""Register a folder of already-downloaded images into the §6-A image bank.

Use this for the **label-reliable scale-up** path: once you've downloaded a
dataset (PTB-XL, NIH ChestX-ray14, Open-i…) and sorted files that share a
finding into one folder, point this tool at that folder to bulk-add manifest
entries with shared metadata. See ``docs/image_bank.md`` for dataset steps and
licensing.

Files are COPIED into ``image_bank/<modality>/`` and catalogued with the
provenance you pass, so the manifest stays the single source of truth.

Example (a folder of NIH pneumonia CXRs you already de-identified/verified):
    python tools/ingest_local_images.py ~/nih/pneumonia \\
        --modality CXR --normality abnormal \\
        --findings pneumonia consolidation \\
        --disease-keywords pneumonia \\
        --source "NIH ChestX-ray14" \\
        --source-url https://nihcc.app.box.com/v/ChestXray-NIHCC \\
        --license "CC0 1.0" --attribution "NIH Clinical Center" \\
        --verified

Idempotent by content hash: a file already in the manifest is skipped.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK_DIR = os.path.join(ROOT, "image_bank")
MANIFEST = os.path.join(BANK_DIR, "manifest.json")
VALID_MODALITIES = {"ECG", "CXR", "XR", "US", "ECHO", "CT", "MRI", "ENDO", "NM"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def load_manifest():
    try:
        with open(MANIFEST, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("entries"), list):
            data["entries"] = []
        return data
    except (OSError, ValueError):
        return {"schema_version": 1, "entries": []}


def save_manifest(data):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


def main():
    ap = argparse.ArgumentParser(description="Bulk-register local images into the image bank.")
    ap.add_argument("directory", help="folder of images sharing the metadata below")
    ap.add_argument("--modality", required=True, choices=sorted(VALID_MODALITIES))
    ap.add_argument("--normality", required=True, choices=["normal", "abnormal"])
    ap.add_argument("--findings", nargs="*", default=[], help="documented ground-truth findings")
    ap.add_argument("--disease-keywords", nargs="*", default=[], help="diseases this illustrates")
    ap.add_argument("--source", default="local", help="dataset name")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--license", default="", help="license of these files (verify before redistributing)")
    ap.add_argument("--attribution", default="")
    ap.add_argument("--caption", default="", help="shared caption (defaults to findings)")
    ap.add_argument("--verified", action="store_true", help="mark entries as human-verified findings")
    ap.add_argument("--limit", type=int, default=0, help="cap number of files (0 = all)")
    args = ap.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"Not a directory: {args.directory}")

    files = sorted(
        f for f in os.listdir(args.directory)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
        and os.path.isfile(os.path.join(args.directory, f))
    )
    if args.limit:
        files = files[: args.limit]
    if not files:
        sys.exit("No image files found in that directory.")

    manifest = load_manifest()
    existing_sha = {e.get("sha256") for e in manifest["entries"] if e.get("sha256")}
    dest_dir = os.path.join(BANK_DIR, args.modality.lower())
    os.makedirs(dest_dir, exist_ok=True)

    added = 0
    for name in files:
        src = os.path.join(args.directory, name)
        with open(src, "rb") as f:
            content = f.read()
        sha = hashlib.sha256(content).hexdigest()
        if sha in existing_sha:
            continue
        ext = os.path.splitext(name)[1].lower()
        entry_id = f"{args.modality.lower()}-{args.normality}-{sha[:10]}"
        rel = os.path.join(args.modality.lower(), entry_id + ext)
        shutil.copyfile(src, os.path.join(BANK_DIR, rel))
        manifest["entries"].append({
            "id": entry_id,
            "file": rel.replace("\\", "/"),
            "modality": args.modality,
            "normality": args.normality,
            "findings": args.findings,
            "disease_keywords": args.disease_keywords,
            "source": args.source,
            "source_url": args.source_url,
            "license": args.license,
            "attribution": args.attribution,
            "deidentified": True,
            "synthetic": False,
            "verified": bool(args.verified),
            "sha256": sha,
            "caption": args.caption or "、".join(args.findings) or entry_id,
        })
        existing_sha.add(sha)
        added += 1

    if added:
        save_manifest(manifest)
    print(f"Registered {added} image(s) into {args.modality}. Manifest now has {len(manifest['entries'])} entries.")


if __name__ == "__main__":
    main()
