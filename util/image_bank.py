"""Real de-identified diagnostic-image retrieval (proposal §6-A).

Feedback repeatedly asked for **real images to practice reading** ("心電圖、X 光
可以放實際圖片練習判讀"). The authoritative constraint (documented in
UPGRADE_PROPOSAL §6) is that diagnostic images must be **real, de-identified
images from open banks — never AI-generated** — because radiologists misread
synthetic CXRs 22–38 % of the time and generative models hallucinate pathology,
so training on generated films teaches students to read fabricated findings.

This module is the retrieval layer over a **local image bank**: a directory
(default ``image_bank/``, override with ``PATIENZ_IMAGE_BANK_DIR``) holding a
``manifest.json`` catalog plus the image files. The bank is populated by
``tools/fetch_image_bank.py`` (open-licensed starter set) and/or by ingesting
PTB-XL / NIH ChestX-ray14 / Open-i locally (see ``docs/image_bank.md``); nothing
is bundled in the repo, so the app degrades gracefully to text-only when the
bank is empty or a referenced file is missing.

Pedagogical safety — the retrieval NEVER shows a possibly-misleading image. An
image is eligible only when ALL of these hold; otherwise ``find_image`` returns
``None`` and the page shows the text report alone (we never guess):

- **verified**: the entry is human-reviewed (``"verified": true``). Auto-fetched
  entries ship ``verified:false`` and are inert until a person confirms them, so
  a mislabeled Commons hit can never surface.
- **modality** is a hard filter (an ECG request never returns a CXR).
- **abnormal** images must have their *own documented finding evidenced in this
  exam's report text* — matched against the film's ground-truth ``findings`` (not
  the case's headline disease), so an ischemic-stroke report can never pull a
  hemorrhage film and an ECG reporting atrial fibrillation can never pull a STEMI
  tracing. When the report evidences no finding we show text only.
- **normal** images: single-region modalities (ECG, CXR, ECHO) are safe as-is; a
  multi-anatomy normal study (US, XR, CT, MRI, ENDO, NM) must match the ordered
  study's region, so a normal *abdominal* ultrasound is never shown for a
  *carotid* order.

The image is always presented as a *representative* real example of the reported
finding (captioned with its own ground-truth finding + provenance + license),
explicitly "非本虛擬病人本人", so it is truthful read-practice rather than a
claimed film of the synthetic patient.

Streamlit-free so it stays unit-testable.
"""
import json
import os
import re

import util.grading_normalize as gn

DEFAULT_BANK_DIR = "image_bank"
MANIFEST_NAME = "manifest.json"

# Canonical modalities the bank distinguishes. Chest vs. non-chest X-ray are
# separated because a chest film and a limb film are not interchangeable.
MODALITIES = {"ECG", "CXR", "XR", "US", "ECHO", "CT", "MRI", "ENDO", "NM"}

# Modalities that image a single fixed region, so a *normal* film of that
# modality is a safe "this is what normal looks like" regardless of the exact
# order. Every other modality spans multiple body regions (an abdominal vs a
# carotid ultrasound, a limb vs a spine film) and so a normal image must be
# region-matched to the ordered study before it may be shown.
SINGLE_REGION_MODALITIES = {"ECG", "CXR", "ECHO"}

# Ordered (keywords -> modality); first match on the exam's English/Chinese item
# text wins. More specific entries (chest x-ray, echocardiography, CTA) precede
# the generic ones (x-ray, ultrasound, ct).
_MODALITY_RULES = [
    (("electrocardiograph", "ecg", "ekg", "心電圖"), "ECG"),
    (("chest x-ray", "chest xray", "cxr", "胸部x"), "CXR"),
    (("kub", "abdominal x-ray", "abdominal xray", "extremity x-ray", "extremity xray",
      "spine x-ray", "spine xray", "x-ray", "xray", "x光", "radiograph"), "XR"),
    (("echocardiograph", "echocardiogram", "心臟超音波"), "ECHO"),
    (("ultrasonograph", "ultrasound", "doppler", "sonograph", "超音波"), "US"),
    (("angiography", "cta", "ct ", "chest ct", "abdominal ct", "head ct", "spine ct",
      "電腦斷層"), "CT"),
    (("mri", "mra", "magnetic resonance", "磁振", "核磁"), "MRI"),
    (("endoscop", "colonoscop", "gastroscop", "內視鏡", "大腸鏡"), "ENDO"),
    (("bone scan", "pet scan", "scintigraph", "核醫", "骨骼掃描"), "NM"),
]

# A latin surface form must be at least this long to be looked up as a substring
# of free-text, so short abbreviations ("mi", "pe", "rf", "ich", "cva") cannot
# spuriously match inside unrelated words. CJK is information-dense, so 2 chars
# ("氣胸", "骨折") are specific enough.
_MIN_LATIN_FORM = 4
_MIN_CJK_FORM = 2


def resolve_modality(*texts) -> str:
    """Map exam item text (subcategory + English/Chinese names) to a canonical
    modality, or ``None`` for non-imaging exams (blood gas, PFT, EMG…).

    ``ct`` is matched with a trailing space / phrase context to avoid hitting
    substrings like "s**ct**ion"; the caller passes discrete item names so this
    is safe in practice.
    """
    blob = " ".join(t for t in texts if t).lower()
    if not blob.strip():
        return None
    for keywords, modality in _MODALITY_RULES:
        if any(k in blob for k in keywords):
            return modality
    return None


def enabled() -> bool:
    return os.environ.get("PATIENZ_DISABLE_IMAGE_BANK", "").lower() not in ("1", "true", "yes")


def bank_dir() -> str:
    return os.environ.get("PATIENZ_IMAGE_BANK_DIR", DEFAULT_BANK_DIR)


def load_manifest(directory=None) -> dict:
    """Load and lightly validate the bank manifest. Any problem (missing file,
    bad JSON, wrong shape) yields an empty catalog rather than an error, so a
    fresh clone with no bank simply runs text-only."""
    directory = directory or bank_dir()
    path = os.path.join(directory, MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"schema_version": 1, "entries": []}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        entries = []
    return {"schema_version": data.get("schema_version", 1) if isinstance(data, dict) else 1,
            "entries": [e for e in entries if isinstance(e, dict) and e.get("modality") in MODALITIES]}


def image_path(entry, directory=None) -> str:
    """Absolute path to an entry's image file, or ``None`` if the file is not
    present on disk (manifest may reference files not yet fetched)."""
    directory = directory or bank_dir()
    rel = entry.get("file")
    if not rel:
        return None
    path = os.path.normpath(os.path.join(directory, rel))
    return path if os.path.isfile(path) else None


def _norm(s) -> str:
    """Lowercase and drop everything but alphanumerics (CJK kept), so spacing /
    punctuation / case do not affect substring lookups."""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _form_long_enough(nf) -> bool:
    if not nf:
        return False
    return len(nf) >= (_MIN_LATIN_FORM if nf.isascii() else _MIN_CJK_FORM)


def _evidence_count(terms, text) -> int:
    """How many of ``terms`` (curated finding/region labels) are *evidenced* in
    free-text ``text``.

    For each term we expand it to its concept's accepted surface forms (中英 /
    synonym tolerant, via the grading normalizer) and test whether any
    sufficiently-specific form appears as a substring of the normalized text.
    This looks a *controlled vocabulary up inside narrative* — the safe
    direction — so it never bridges two unrelated concepts the way a
    substring-tolerant term-vs-term compare would ('stroke' ⊄ 'heatstroke' here,
    because we only test the entry's own finding forms, never the case's).
    """
    norm_text = _norm(text)
    if not norm_text:
        return 0
    n = 0
    for t in terms:
        if not t:
            continue
        forms = gn.surface_forms(t) or [t]
        if any(_form_long_enough(_norm(f)) and _norm(f) in norm_text for f in forms):
            n += 1
    return n


def _region_words(entry):
    """Region/anatomy descriptors an entry advertises: split its findings,
    disease_keywords and caption into word tokens (latin words + CJK runs)."""
    blob = list(entry.get("findings") or []) + list(entry.get("disease_keywords") or [])
    if entry.get("caption"):
        blob.append(entry["caption"])
    words = []
    for t in blob:
        words += [w for w in re.split(r"[^0-9A-Za-z一-鿿]+", str(t)) if w]
    return words


# Descriptor tokens that say "normal / how it was taken", not *which region*, so
# they must not be treated as a region match for a normal study.
_REGION_STOPWORDS = {"normal", "unremarkable", "study", "view", "film", "image",
                     "images", "scan", "radiograph", "ultrasound", "sonography",
                     "sonogram", "正常"}


def _region_score(entry, item_terms) -> int:
    """Overlap between an entry's advertised region words and the ordered study's
    item text — used to gate *normal* multi-anatomy images so a normal abdominal
    ultrasound is not shown for a carotid order."""
    item_norm = _norm(" ".join(item_terms))
    if not item_norm:
        return 0
    n = 0
    for w in _region_words(entry):
        if w.lower() in _REGION_STOPWORDS:
            continue
        nw = _norm(w)
        if _form_long_enough(nw) and nw in item_norm:
            n += 1
    return n


def find_image(modality, has_abnormal, *, report_text=None, item_terms=None,
               directory=None, manifest=None):
    """Best real image for an exam result, or ``None`` if none clears the bar.

    Args:
        modality: canonical modality (see :func:`resolve_modality`).
        has_abnormal: the examiner's [NORMAL]/[ABNORMAL] verdict for this result.
            The caller must only ask when the verdict is *known* (an explicit
            tag was emitted); an unknown verdict must fall back to text-only.
        report_text: this exam's narrative report. For an abnormal result the
            chosen film's own ``findings`` must be evidenced here.
        item_terms: the ordered study's item names (English + Chinese), used to
            region-match *normal* multi-anatomy studies.
    """
    if not modality or not enabled():
        return None
    directory = directory or bank_dir()
    man = manifest if manifest is not None else load_manifest(directory)
    want = "abnormal" if has_abnormal else "normal"
    item_terms = [t for t in (item_terms or []) if t]

    scored = []
    for e in man["entries"]:
        if e.get("modality") != modality:
            continue
        if (e.get("normality") or "normal") != want:
            continue
        if not e.get("verified"):           # never show an unreviewed image
            continue
        if image_path(e, directory) is None:  # file not on disk → unusable
            continue

        if want == "abnormal":
            # The film's OWN documented finding must be evidenced in this report,
            # so we only ever show a film of the pathology this exam described.
            score = _evidence_count(e.get("findings") or [], report_text)
            if score == 0:
                continue
        else:
            # A normal single-region film is safe as-is; a multi-anatomy normal
            # film must match the ordered study's region or we don't show it.
            if modality in SINGLE_REGION_MODALITIES:
                score = 1
            else:
                score = _region_score(e, item_terms)
                if score == 0:
                    continue
        scored.append((score, e))

    if not scored:
        return None
    # Highest evidence/region score wins; deterministic tiebreak by id.
    scored.sort(key=lambda t: (-t[0], str(t[1].get("id", ""))))
    return scored[0][1]


def describe(entry, directory=None) -> dict:
    """Render-ready provenance for the UI: resolved path + caption + a
    real/synthetic badge + license/attribution line."""
    synthetic = bool(entry.get("synthetic"))
    if synthetic:
        badge = "🧪 AI 生成示意圖（非真實影像，僅供外觀示意）"
    else:
        badge = "🩻 真實去識別化影像範例（非本虛擬病人本人，供判讀練習）"
    bits = []
    if entry.get("source"):
        bits.append(entry["source"])
    if entry.get("license"):
        bits.append(entry["license"])
    if entry.get("attribution"):
        bits.append(entry["attribution"])
    provenance = "　·　".join(bits)
    return {
        "path": image_path(entry, directory),
        "caption": entry.get("caption") or "、".join(entry.get("findings") or []) or entry.get("id", ""),
        "badge": badge,
        "provenance": provenance,
        "source_url": entry.get("source_url", ""),
        "synthetic": synthetic,
    }
