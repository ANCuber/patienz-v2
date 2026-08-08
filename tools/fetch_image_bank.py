#!/usr/bin/env python3
"""Populate the §6-A real-image bank from open-licensed sources.

Downloads a small **starter set** of real, de-identified diagnostic images from
**Wikimedia Commons** (stable MediaWiki API), keeping only permissively-licensed
files (Public Domain / CC0 / CC-BY / CC-BY-SA), and writes matching entries —
with full provenance (source URL, license, author) — into
``image_bank/manifest.json``.

Why Commons for the starter set: individual files carry explicit per-file
licenses and de-identification, and the API contract is rock-stable — no
dataset-scale download or credentialing. For **label-reliable abnormal images
at scale**, ingest PTB-XL (ECG) and NIH ChestX-ray14 / Open-i (CXR) locally
instead; see ``docs/image_bank.md``.

Pedagogical-safety notes baked in:
- Every auto-fetched entry is flagged ``"verified": false``. The app's retrieval
  (``util/image_bank.find_image``) **refuses to display any unverified entry**,
  so a freshly-fetched image is inert — it will not appear to a student until a
  human reviews the actual file and sets ``"verified": true`` in the manifest.
- Once verified, an abnormal image is still only shown when its documented
  ``findings`` are evidenced in that exam's report text, so it can never appear
  for a report describing a different pathology.

To activate fetched images: open ``image_bank/manifest.json``, confirm each file
truly depicts its recorded finding, and set that entry's ``"verified": true``.

Usage (network required — run this yourself, not in CI):
    python tools/fetch_image_bank.py                 # fetch default starter set
    python tools/fetch_image_bank.py --max 3         # cap files per query
    python tools/fetch_image_bank.py --only ECG CXR  # subset of modalities
    python tools/fetch_image_bank.py --dry-run       # list matches, download nothing

Idempotent: existing manifest ids / already-downloaded files are skipped.
"""
import argparse
import hashlib
import json
import os
import sys
import time


def _requests():
    """Lazily import requests only when we actually hit the network, so the
    pure helpers (e.g. _license_ok) stay importable/unit-testable without it."""
    try:
        import requests
        return requests
    except ImportError:
        sys.exit("This tool needs 'requests' (pip install requests).")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK_DIR = os.path.join(ROOT, "image_bank")
MANIFEST = os.path.join(BANK_DIR, "manifest.json")

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "PatienzImageBankFetcher/1.0 (educational OSCE simulator; contact: local admin)"

# Licenses we accept (matched case-insensitively as substrings of the Commons
# LicenseShortName, e.g. "CC BY-SA 4.0", "CC0", "Public domain"). NonCommercial
# (-nc) and NoDerivatives (-nd) variants are rejected first (see _license_ok):
# "cc by" is a substring of "CC BY-NC 2.0", so the allow-list alone would wrongly
# admit non-free / non-redistributable content.
ALLOWED_LICENSE_HINTS = ("public domain", "cc0", "cc by", "cc-by")
DENIED_LICENSE_HINTS = ("-nc", "-nd", "noncommercial", "noderiv", "no deriv")

# Curated queries. Each: (modality, normality, findings, disease_keywords,
# [commons search strings]). Keep findings/keywords conservative and specific.
QUERIES = [
    ("ECG", "normal", ["normal sinus rhythm"], [],
     ["normal 12-lead ECG", "normal electrocardiogram"]),
    ("ECG", "abnormal", ["ST elevation", "myocardial infarction"],
     ["acute coronary syndrome", "STEMI", "myocardial infarction"],
     ["ST elevation myocardial infarction ECG", "STEMI electrocardiogram"]),
    ("ECG", "abnormal", ["atrial fibrillation"], ["atrial fibrillation"],
     ["atrial fibrillation ECG"]),
    ("CXR", "normal", ["normal chest"], [],
     ["normal chest radiograph", "normal PA chest x-ray"]),
    ("CXR", "abnormal", ["pneumonia", "consolidation"], ["pneumonia"],
     ["pneumonia chest radiograph"]),
    ("CXR", "abnormal", ["pneumothorax"], ["pneumothorax"],
     ["pneumothorax chest x-ray"]),
    ("CXR", "abnormal", ["pleural effusion"], ["pleural effusion", "heart failure"],
     ["pleural effusion chest radiograph"]),
    ("XR", "abnormal", ["fracture"], ["fracture"],
     ["bone fracture radiograph"]),
    # Keep the disease_keywords specific to the actual finding: an umbrella
    # "stroke" keyword would bridge a *hemorrhagic* film to an *ischemic* case
    # (opposite management), so it is deliberately excluded.
    ("CT", "abnormal", ["intracranial hemorrhage"], ["intracranial hemorrhage", "hemorrhagic stroke"],
     ["intracranial hemorrhage CT head"]),
    ("US", "normal", ["normal abdomen"], [],
     ["abdominal ultrasound normal"]),
]

IMAGE_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_BYTES = 12 * 1024 * 1024  # skip anything implausibly large for a teaching image


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


def commons_search(query, limit):
    """Return imageinfo dicts for File-namespace search hits."""
    params = {
        "action": "query", "format": "json",
        "generator": "search", "gsrsearch": query, "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size|user",
    }
    r = _requests().get(COMMONS_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    pages = (r.json().get("query", {}) or {}).get("pages", {}) or {}
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [None])[0]
        if ii:
            ii["_title"] = p.get("title", "")
            out.append(ii)
    return out


def _license_ok(ii):
    ext = ii.get("extmetadata", {}) or {}
    short = (ext.get("LicenseShortName", {}) or {}).get("value", "").lower()
    if any(bad in short for bad in DENIED_LICENSE_HINTS):
        return False  # reject CC BY-NC / -ND (non-free / non-redistributable)
    return any(h in short for h in ALLOWED_LICENSE_HINTS)


def _clean(text):
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _attribution(ii):
    ext = ii.get("extmetadata", {}) or {}
    artist = _clean((ext.get("Artist", {}) or {}).get("value", ""))
    return artist or ii.get("user", "") or "Unknown"


def fetch(modality_filter, per_query, dry_run):
    manifest = load_manifest()
    existing_ids = {e.get("id") for e in manifest["entries"]}
    existing_sha = {e.get("sha256") for e in manifest["entries"] if e.get("sha256")}
    added = 0

    for modality, normality, findings, keywords, searches in QUERIES:
        if modality_filter and modality not in modality_filter:
            continue
        os.makedirs(os.path.join(BANK_DIR, modality.lower()), exist_ok=True)
        got = 0
        for query in searches:
            if got >= per_query:
                break
            try:
                hits = commons_search(query, per_query * 3)
            except Exception as e:
                print(f"  ! search failed [{query}]: {e}")
                continue
            for ii in hits:
                if got >= per_query:
                    break
                mime = ii.get("mime", "")
                if mime not in IMAGE_MIMES or not _license_ok(ii):
                    continue
                if int(ii.get("size", 0) or 0) > MAX_BYTES:
                    continue
                url = ii.get("url")
                title = ii.get("_title", "").replace("File:", "")
                ext = IMAGE_MIMES[mime]
                base = f"{modality.lower()}-{normality}-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
                if base in existing_ids:
                    continue
                print(f"  {'(dry) ' if dry_run else ''}[{modality}/{normality}] {title[:70]}  <{_clean((ii.get('extmetadata',{}).get('LicenseShortName',{}) or {}).get('value',''))}>")
                if dry_run:
                    got += 1
                    continue
                try:
                    resp = _requests().get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
                    resp.raise_for_status()
                    content = resp.content
                except Exception as e:
                    print(f"    ! download failed: {e}")
                    continue
                sha = hashlib.sha256(content).hexdigest()
                if sha in existing_sha:
                    continue
                rel = os.path.join(modality.lower(), base + ext)
                with open(os.path.join(BANK_DIR, rel), "wb") as f:
                    f.write(content)
                entry = {
                    "id": base,
                    "file": rel.replace("\\", "/"),
                    "modality": modality,
                    "normality": normality,
                    "findings": findings,
                    "disease_keywords": keywords,
                    "source": "Wikimedia Commons",
                    "source_url": ii.get("descriptionurl", ""),
                    "license": _clean((ii.get("extmetadata", {}).get("LicenseShortName", {}) or {}).get("value", "")),
                    "attribution": _attribution(ii),
                    "deidentified": True,
                    "synthetic": False,
                    "verified": False,
                    "caption": _clean((ii.get("extmetadata", {}).get("ImageDescription", {}) or {}).get("value", ""))[:200] or title,
                }
                manifest["entries"].append(entry)
                existing_ids.add(base)
                existing_sha.add(sha)
                added += 1
                got += 1
                time.sleep(0.5)  # be polite to the API

    if not dry_run and added:
        save_manifest(manifest)
    print(f"\n{'(dry run) ' if dry_run else ''}Added {added} image(s). Manifest now has {len(manifest['entries'])} entries.")
    if added:
        print("NOTE: auto-fetched entries are flagged verified:false and will NOT be shown "
              "to students until you review each file and set \"verified\": true in "
              "image_bank/manifest.json.")


def main():
    ap = argparse.ArgumentParser(description="Populate the §6-A real-image bank from Wikimedia Commons.")
    ap.add_argument("--max", type=int, default=2, help="max files per query (default 2)")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these modalities (e.g. ECG CXR)")
    ap.add_argument("--dry-run", action="store_true", help="list matches without downloading")
    args = ap.parse_args()
    os.makedirs(BANK_DIR, exist_ok=True)
    only = {m.upper() for m in args.only} if args.only else None
    print(f"Fetching into {BANK_DIR} (max {args.max}/query){' [dry run]' if args.dry_run else ''}...")
    fetch(only, args.max, args.dry_run)


if __name__ == "__main__":
    main()
