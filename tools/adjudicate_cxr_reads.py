#!/usr/bin/env python3
"""Rule-based adjudication of the two cached vision reads per chest film.

The review workflow runs three agents per film: a label-aware reader (A), a
blind reader (B) and an adjudicator (C) that reconciles them and writes the
sidecar. When C could not run (account quota), A and B are still in the
workflow journal. This tool folds those two reads plus the radiologist's box
position into a sidecar of the same shape, marked ``ai-vision-2reader+rule`` so
it is distinguishable from (and is overwritten by) a real adjudication.

Rules are deliberately conservative:
* side = the majority of {radiologist box, reader A, reader B}; "full" only
  when every available vote agrees, "partial" with one dissent, "conflict"
  otherwise or when the label-aware reader could not see the finding;
* devices = union of both readers, mapped to zh-TW and de-duplicated on the
  device type; side markers / technologist initials / artifacts are not devices;
* treatment_evidence if either reader saw it; a film is only first-presentation
  suitable when neither reader saw treatment, A could see the finding and the
  side is not in conflict.

Usage (PowerShell, repo root):
  python tools/adjudicate_cxr_reads.py --journal <journal.jsonl> --items <cxr_args.json>
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from util import image_bank as ib  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "image_bank", "annotations", "cxr")

# Radiographic side markers, technologist initials, exposure artifacts and the
# like are on the film, not in the patient; readers list them, so drop them.
NOT_A_DEVICE = re.compile(
    r"marker|initials|\blabel\b|\btag\b|annotation|artifact|artefact|\bband\b|density\b", re.I)

DEVICE_ZH = [
    (r"chest tube|thoracostomy|pleural drain|intercostal", "胸管／肋膜引流管"),
    (r"pigtail", "豬尾巴導管"),
    (r"\bport\b|port-a|portacath|infusion port|injection port|chemo(therapy)? port", "植入式人工血管（port-A）"),
    (r"central venous|\bcvc\b|internal jugular|subclavian.*(line|catheter)|\bpicc\b|hickman|dialysis catheter",
     "中心靜脈導管"),
    (r"pacemaker|\bicd\b|defibrillator|\bcrt\b", "心臟節律器／去顫器"),
    (r"sternotomy|sternal wire|cerclage", "胸骨切開術鋼絲"),
    (r"endotracheal|\bett\b|tracheostomy|\btracheal tube\b", "氣管內管／氣切管"),
    (r"nasogastric|\bng tube\b|feeding tube|orogastric", "鼻胃管"),
    (r"\becg\b|\bekg\b|electrode|lead wire|monitoring lead|telemetry", "心電圖監測電極及導線"),
    (r"surgical clip|\bclips?\b|suture|staple", "手術夾／縫合材"),
    (r"button|snap|necklace|jewel|zipper|clothing|\bbra\b|pendant", "衣物／飾品陰影"),
    (r"prosthetic valve|\bvalve\b", "人工瓣膜"),
    (r"\bstent\b", "支架"),
    (r"spinal (rod|screw|hardware)|orthop|prosthesis|plate\b", "骨科植入物"),
    (r"catheter|\bline\b|\btube\b|\bdrain", "導管／管路"),
]


def device_zh(name):
    """zh-TW name for a reader's device type, or None when it is not a device."""
    low = (name or "").lower().strip()
    if not low or NOT_A_DEVICE.search(low) or ib.NOT_A_DEVICE_RE.search(low):
        return None
    for pat, zh in DEVICE_ZH:
        if re.search(pat, low):
            return zh
    return name


def load_reads(journal_path):
    """{label -> result} for every completed reader in the journal."""
    started = {}
    reads = {}
    for line in io.open(journal_path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "started":
            started[d.get("key")] = d.get("label")
        elif d.get("type") == "result" and d.get("result"):
            label = started.get(d.get("key"))
            if label:
                reads[label] = d["result"]
    return reads


ALIAS = {"effusion": ["effusion", "fluid"], "cardiomegaly": ["cardiomegaly", "enlarged", "cardiac silhouette"],
         "pneumothorax": ["pneumothorax"], "atelectasis": ["atelectasis", "collapse", "volume loss"],
         "consolidation": ["consolidation", "airspace"], "infiltrate": ["infiltrate", "opacity", "opacities"],
         "nodule": ["nodule", "nodular"], "mass": ["mass"], "pneumonia": ["pneumonia", "consolidation", "airspace"],
         "edema": ["edema", "oedema", "congestion"], "emphysema": ["emphysema", "hyperinflat"],
         "fibrosis": ["fibrosis", "fibrotic", "reticular", "interstitial"], "hernia": ["hernia", "herniat"],
         "thickening": ["thickening", "pleural"]}


def blind_side_for(read_b, finding):
    """Side (and zone) reader B gave the labelled finding, if B found it blind."""
    keys = set()
    for w in re.split(r"[^a-z]+", (finding or "").lower()):
        if len(w) > 3:
            keys.update(ALIAS.get(w, [w]))
    for ab in read_b.get("abnormalities") or []:
        text = (ab.get("finding") or "").lower()
        if any(k in text for k in keys):
            return ab.get("laterality"), ab.get("zone")
    return None, None


def adjudicate(item, a, b):
    finding = item["finding"]
    boxes = item.get("radiologist_boxes") or []
    bbox_side = boxes[0]["patient_side"] if boxes else None
    a_side = (a or {}).get("laterality")
    a_visible = (a or {}).get("target_finding_visible", "uncertain")
    b_side, b_zone = blind_side_for(b or {}, finding) if b else (None, None)

    sided = finding not in ("cardiomegaly", "no acute cardiopulmonary finding")
    if not sided:
        side, agreement = "none", "full"
    else:
        votes = [s for s in (bbox_side, a_side, b_side) if s in ("right", "left", "bilateral")]
        if not votes:
            side, agreement = "uncertain", "conflict"
        else:
            side = max(set(votes), key=votes.count)
            n_agree, n_votes = votes.count(side), len(votes)
            if n_votes >= 2 and n_agree == n_votes:
                agreement = "full"
            elif n_agree >= 2:
                agreement = "partial"
            else:
                agreement = "conflict"
        if a_visible == "no" and agreement != "full":
            agreement = "conflict"

    devices, seen = [], set()
    for read in (a, b):
        for d in (read or {}).get("devices") or []:
            if not isinstance(d, dict):
                continue
            zh = device_zh(d.get("type"))
            if not zh or zh.lower() in seen:
                continue
            seen.add(zh.lower())
            devices.append({"type": d.get("type", ""), "location": d.get("location", ""), "zh": zh})

    other = []
    for read in (a, b):
        for ab in (read or {}).get("abnormalities") or []:
            t = (ab.get("finding") or "").strip()
            if t and t not in other:
                other.append(t)

    treatment = bool((a or {}).get("treatment_evidence")) or bool((b or {}).get("treatment_evidence"))
    suitable = (not treatment) and a_visible != "no" and agreement != "conflict"
    reason = []
    if treatment:
        reason.append("讀片員見到治療裝置（胸管／引流管等）")
    if a_visible == "no":
        reason.append("知標籤的讀片員看不到該所見")
    if agreement == "conflict":
        reason.append("側別判讀不一致")
    desc_parts = [x for x in ((b or {}).get("description_zh"), (a or {}).get("description_zh")) if x]

    try:
        size = float((a or {}).get("size_cm", -1))
    except (TypeError, ValueError):
        size = -1.0
    return {
        "id": item["id"],
        "reviewed_by": "ai-vision-2reader+rule",
        "view": (a or {}).get("view") or (b or {}).get("view") or item.get("view") or "",
        "technical_quality": (b or {}).get("technical_quality") or (a or {}).get("technical_quality") or "",
        "target": {"finding": finding, "visible": a_visible, "laterality": side,
                   "zone": (a or {}).get("zone") or b_zone or "uncertain",
                   "size_cm": size, "confidence": float((a or {}).get("confidence") or 0)},
        "laterality_sources": {"bbox": bbox_side, "reader_a": a_side, "reader_b": b_side,
                               "adjudicator": None},
        "agreement": agreement,
        "devices": devices,
        "other_findings": other,
        "treatment_evidence": treatment,
        "first_presentation_suitable": suitable,
        "suitability_reason": "；".join(reason) or "無治療裝置、所見可見、側別一致",
        "description_zh": " ".join(desc_parts),
        "label_concern": "知標籤的讀片員無法看到該所見" if a_visible == "no" else "",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--items", required=True, help="the cxr_args.json the workflow was fed")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--overwrite-llm", action="store_true",
                    help="also overwrite sidecars written by the real adjudicator (default: keep them)")
    args = ap.parse_args()

    reads = load_reads(args.journal)
    items = json.load(io.open(args.items, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)
    written = kept = skipped = 0
    for item in items:
        a, b = reads.get("A:" + item["id"]), reads.get("B:" + item["id"])
        if not a and not b:
            skipped += 1
            continue
        path = os.path.join(args.out, item["id"] + ".json")
        if os.path.exists(path) and not args.overwrite_llm:
            try:
                if json.load(io.open(path, encoding="utf-8")).get("reviewed_by") == "ai-vision-3reader":
                    kept += 1
                    continue
            except (OSError, ValueError):
                pass
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(adjudicate(item, a, b), f, ensure_ascii=False, indent=1)
        written += 1
    print("rule-adjudicated %d film(s); kept %d LLM-adjudicated; %d had no cached read"
          % (written, kept, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
