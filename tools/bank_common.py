#!/usr/bin/env python3
"""Shared plumbing for the §6-A dataset→image-bank builders.

``build_ptbxl_bank.py`` (ECG) and ``build_nih_cxr_bank.py`` (CXR) follow the same
shape: stratify a public dataset by **sex × age-band**, pick the best real,
de-identified study per cell, write manifest entries, and emit an honest coverage
report that says out loud what the data could *not* cover. Everything here is the
part of that shape which is dataset-independent, so neither builder re-implements
(or silently diverges on) age banding, sex normalization, manifest I/O, or the
report format.

Pure/stdlib-only, hence directly unit-testable.
"""
import json
import os

STATUS_ORDER = {"none": 0, "partial": 1, "ok": 2}


# ---------- demographic stratification ----------
def parse_bands(spec):
    """``"0,19,40,60,80"`` -> ``[0, 19, 40, 60, 80]`` = bands [0,19) [19,40)
    [40,60) [60,80) [80,inf). A leading 0 is implied, order/duplicates forgiven."""
    bounds = sorted({int(x) for x in str(spec).split(",") if str(x).strip() != ""})
    bounds = [b for b in bounds if b >= 0]
    if not bounds or bounds[0] != 0:
        bounds = [0] + bounds
    return bounds


def age_band(age, bounds):
    """Index of the band ``age`` falls in, or ``None`` when age is unusable.

    Ages above the top bound land in the open-ended last band — which is what we
    want for de-identification sentinels (PTB-XL records >89 as ``300``); callers
    that must reject implausible ages do so *before* calling this."""
    try:
        a = float(age)
    except (TypeError, ValueError):
        return None
    idx = 0
    for i, b in enumerate(bounds):
        if a >= b:
            idx = i
    return idx


def band_labels(bounds):
    """Human labels for the bands, e.g. ``['0-18', '19-39', ..., '80+']``."""
    out = []
    for i, b in enumerate(bounds):
        if i + 1 < len(bounds):
            out.append(f"{b}-{bounds[i + 1] - 1}")
        else:
            out.append(f"{b}+")
    return out


def norm_sex(v):
    """Dataset sex value -> ``'M'``/``'F'``/``None``.

    Covers both conventions in play: PTB-XL's numeric ``0``/``1`` and NIH
    ChestX-ray14's ``M``/``F``."""
    s = str(v).strip()
    if s in ("0", "0.0", "M", "m", "male", "Male", "MALE", "男"):
        return "M"
    if s in ("1", "1.0", "F", "f", "female", "Female", "FEMALE", "女"):
        return "F"
    return None


def cells_total(bounds):
    return 2 * len(bounds)


# ---------- coverage accounting ----------
def coverage_status(info):
    """``none`` (no eligible study at all) / ``partial`` (some cells) / ``ok``
    (every sex × age-band cell filled)."""
    if info.get("n_available", 0) == 0:
        return "none"
    if info.get("cells_available", 0) >= info.get("cells_total", 0):
        return "ok"
    return "partial"


def summarize(rows):
    """Aggregate per-class coverage rows into the report headline numbers."""
    n = len(rows)
    return {
        "n_classes": n,
        "n_any": sum(1 for r in rows if r["info"]["n_selected"] > 0),
        "n_ok": sum(1 for r in rows if r["status"] == "ok"),
        "n_partial": sum(1 for r in rows if r["status"] == "partial"),
        "n_none": sum(1 for r in rows if r["status"] == "none"),
        "n_images": sum(r["info"]["n_selected"] for r in rows),
        "cells_filled": sum(r["info"]["n_selected"] for r in rows),
    }


def write_report(path, title, summary_lines, columns, table_rows, footer_lines):
    """Write a markdown coverage report.

    ``table_rows`` are ``(status, secondary_sort_key, [pre-formatted cells])``;
    they are emitted worst-coverage-first so the honest gaps are what you read
    at the top rather than something buried under the successes.
    """
    lines = [f"# {title}", ""]
    lines += [f"- {s}" for s in summary_lines]
    lines += ["", "| " + " | ".join(columns) + " |",
              "|" + "|".join("---" for _ in columns) + "|"]
    for status, key2, cells in sorted(table_rows, key=lambda t: (STATUS_ORDER.get(t[0], 0), t[1])):
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += [""] + [f"> {s}" for s in footer_lines]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- manifest I/O ----------
def load_manifest(bank_dir):
    path = os.path.join(bank_dir, "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("manifest is not an object")
        if not isinstance(data.get("entries"), list):
            data["entries"] = []
        return data
    except (OSError, ValueError):
        return {"schema_version": 1, "entries": []}


def save_manifest(bank_dir, data):
    """Atomic write, so an interrupted build can never leave a half-written
    manifest that the app would then fail to parse (it would silently fall back
    to an empty bank, hiding every image)."""
    path = os.path.join(bank_dir, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
