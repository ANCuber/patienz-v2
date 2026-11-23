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

- **verified**: the entry is cleared for display (``"verified": true``).
  Auto-fetched entries ship ``verified:false`` and are inert until someone
  clears them, so a mislabeled Commons hit cannot surface by accident. This flag
  says *may this be shown*, NOT *has a clinician checked it* — those came apart
  once a bank was deliberately published ahead of review. Who vouched for the
  label is a separate axis (:func:`review_status`), and the page states it on
  screen: a radiologist's bounding box localizes the finding it was handed, it
  does not adjudicate the label, so it is disclosed as un-reviewed, not as
  verified.
- **modality** is a hard filter (an ECG request never returns a CXR).
- **abnormal** images must have their *own documented finding evidenced in this
  exam's report text* — matched against the film's ground-truth ``findings`` (not
  the case's headline disease), so an ischemic-stroke report can never pull a
  hemorrhage film and an ECG reporting atrial fibrillation can never pull a STEMI
  tracing. A finding named only inside a *negated* clause ("肺野未見浸潤") does
  not count as evidence. When the report evidences no finding we show text only.
- **no unclaimed assertion**: the film may not assert what the report did not.
  Mutually-exclusive attribute families (myocardial territory — named directly or
  via the ECG leads; complete vs incomplete block; infarction vs injury vs
  ischemia; left/right/bilateral) are compared, and a film is dropped when it
  contradicts the report *or* localizes where the report never did. A broad
  umbrella term ("myocardial infarction") is likewise never specific enough on
  its own to choose between films, and a film answering the report's conclusion
  outranks one answering a passing remark — so demographics, which are only ever
  a tiebreak, cannot promote a clinically worse film.
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


# A report states its findings clause by clause, and a negation scopes to its
# own clause: 「心臟肥大，肺野未見浸潤」 is two separate claims, not one.
# The enumeration comma 「、」 is deliberately NOT a boundary: it joins the items
# of one list, so 「未見肺水腫、肋膜積水或氣胸」 stays a single negated clause
# (splitting it made 「肋膜積水或氣胸」 positive evidence for an effusion film)
# and 「I、aVL 呈對側性 ST 壓低」 keeps lead I inside the reciprocal clause.
# Brackets are not boundaries either: 「第三度（完全性）房室傳導阻滯」 is one
# claim, and splitting at the parentheses left no clause containing the phrase
# 「完全性房室傳導阻滯」 the third-degree-block film is looked up by.
_CLAUSE_SPLIT = re.compile(r"[,;:.!?，；：。！？\n\r\t]+")

# Cues that make a clause say a finding is ABSENT. A finding named inside such a
# clause is not evidence — without this, 「肺野未見浸潤」 would surface a
# pulmonary-infiltrate film for a report that explicitly ruled infiltrate out,
# which is exactly the misleading image this module exists to prevent.
# Deliberately excluded: 「非」 (「非特異性 ST 變化」 is itself a finding) and a
# bare "negative" ("negative T wave" describes a finding, it does not negate one).
_NEG_CJK = ("未", "無", "沒有", "不見", "排除", "陰性", "否認")
_NEG_LATIN = re.compile(
    r"\b(?:no|not|without|absent|denies|denied|unremarkable|excluded)\b"
    r"|\bnegative for\b|\babsence of\b|\bfree of\b|\bruled out\b")


def _is_negated(clause) -> bool:
    low = str(clause).lower()
    return any(cue in low for cue in _NEG_CJK) or bool(_NEG_LATIN.search(low))


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

    Clauses that negate are dropped before the lookup, so a finding the report
    explicitly excludes never counts. When a negation cannot be scoped (no
    punctuation to split on) the whole span is treated as negated — erring
    toward showing text only, which is the safe direction here.
    """
    clauses = [_norm(c) for c in _CLAUSE_SPLIT.split(str(text or ""))
               if c.strip() and not _is_negated(c)]
    clauses = [c for c in clauses if c]
    if not clauses:
        return 0
    n = 0
    for t in terms:
        if not t:
            continue
        forms = gn.surface_forms(t) or [t]
        nforms = [_norm(f) for f in forms]
        if any(_form_long_enough(nf) and any(nf in c for c in clauses) for nf in nforms):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Mutually-exclusive attributes
#
# Evidence matching alone only answers "is the film's finding mentioned in the
# report?". It never asks the converse — "does the film assert something the
# report did not say, or said differently?" — and that is how an *anteroseptal*
# infarct film came to be captioned onto a report describing II/III/aVF
# elevation. The wrong wall means the wrong artery and the wrong management, so
# these families gate retrieval:
#
#   territory     which myocardial wall (named directly, or via the ECG leads)
#   completeness  complete vs incomplete conduction block
#   lesion        infarction vs injury vs ischemia
#   laterality    right / left / bilateral (annotated by a reviewer; NIH labels
#                 carry no side of their own)
# ---------------------------------------------------------------------------

# Territories are expressed as *wall sets* so partial overlap is expressible:
# a report naming {inferior} is compatible with an inferolateral film (overlap)
# but not with an anteroseptal one (disjoint).
_TERRITORY_TERMS = {
    "anteroseptal": ("anterior", "septal"), "anterolateral": ("anterior", "lateral"),
    "inferolateral": ("inferior", "lateral"), "inferoposterior": ("inferior", "posterior"),
    "inferoposterolateral": ("inferior", "posterior", "lateral"),
    "anterior": ("anterior",), "lateral": ("lateral",), "inferior": ("inferior",),
    "posterior": ("posterior",), "septal": ("septal",),
    "前間壁": ("anterior", "septal"), "前側壁": ("anterior", "lateral"),
    "下側壁": ("inferior", "lateral"), "下後壁": ("inferior", "posterior"),
    "前壁": ("anterior",), "側壁": ("lateral",), "高側壁": ("lateral",),
    # 「間隔」 alone is NOT here: in an ECG report it almost always means an
    # interval (「RR 間隔」「PR 間隔」), and reading it as the septum made a plain
    # atrial-fibrillation report look septally localized.
    "下壁": ("inferior",), "後壁": ("posterior",), "中隔": ("septal",), "室間隔": ("septal",),
}

# Standard lead groupings. Examiner reports routinely name leads rather than
# walls ("II、III、aVF ST 段上升"), so without this the localization is invisible.
_LEAD_TERRITORY = {"ii": ("inferior",), "iii": ("inferior",), "avf": ("inferior",),
                   "v1": ("septal",), "v2": ("septal",), "v3": ("anterior",),
                   "v4": ("anterior",), "v5": ("lateral",), "v6": ("lateral",),
                   "i": ("lateral",), "avl": ("lateral",)}
_LEAD_RE = re.compile(r"\b(aVF|aVL|aVR|V[1-6]|III|II|I)\b", re.I)
_LEAD_RANGE_RE = re.compile(r"\bV([1-6])\s*[-~–—]\s*V?([1-6])\b", re.I)

# Reciprocal ST depression marks the *opposite* wall, not a second infarct
# territory: counting aVL from "aVL 呈對側性 ST 壓低" would turn a pure inferior
# MI report into an inferolateral one.
_RECIPROCAL_CUES = ("對側性", "對應性", "鏡像", "reciprocal", "mirror")

# Ordered value tables — the first pattern that hits decides the family, so the
# more specific spelling must come first ("不完全性" before "完全性").
_COMPLETENESS = (("incomplete", (r"\bincomplete\b", "不完全")),
                 ("complete", (r"\bcomplete\b", "完全性")))
# "ischemia" / "ischaemia" / "ischemic" must all hit. An earlier
# ``isch[ae]emi`` demanded a vowel the American spelling does not have, so every
# "ischemic in X leads" statement carried no lesion value and the
# infarction-vs-ischemia comparison silently could not fire on it.
_LESION = (("injury", (r"\binjur", "損傷")),
           ("infarction", (r"\binfarct", "梗塞")),
           ("ischemia", (r"\bisch[ae]*emi", "缺血")))
_LATERALITY = (("bilateral", (r"\bbilateral\b", "兩側", "雙側")),
               ("right", (r"\bright\b", "右側", "右")),
               ("left", (r"\bleft\b", "左側", "左")))


def _matches_pattern(text, pattern) -> bool:
    """A latin pattern is a regex (word-bounded); a CJK one is a substring."""
    if pattern.isascii():
        return bool(re.search(pattern, text.lower()))
    return pattern in text


def _first_value(text, table):
    """First family value named in ``text``, or None. Table order is priority."""
    t = str(text or "")
    for value, patterns in table:
        if any(_matches_pattern(t, p) for p in patterns):
            return value
    return None


def _sole_value(text, table):
    """The family value named in ``text``, or None when none or *several* are —
    an ambiguous mention must not be allowed to reject an otherwise-good film."""
    t = str(text or "")
    found = {v for v, ps in table if any(_matches_pattern(t, p) for p in ps)}
    return found.pop() if len(found) == 1 else None


def _territory_of(text) -> frozenset:
    """Myocardial walls a piece of text names, directly or through ECG leads."""
    t = str(text or "")
    walls = set()
    for name, ws in _TERRITORY_TERMS.items():
        if _matches_pattern(t, r"\b%s\b" % name if name.isascii() else name):
            walls.update(ws)
    for lo, hi in _LEAD_RANGE_RE.findall(t):
        for n in range(int(lo), int(hi) + 1):
            walls.update(_LEAD_TERRITORY["v%d" % n])
    for lead in _LEAD_RE.findall(t):
        walls.update(_LEAD_TERRITORY.get(lead.lower(), ()))
    return frozenset(walls)


def _positive_clauses(text):
    return [c for c in _CLAUSE_SPLIT.split(str(text or "")) if c.strip() and not _is_negated(c)]


# A report states its conclusion explicitly ("符合急性下壁心肌梗塞"); findings
# mentioned only in passing ("竇性心搏過速") are asides. Without this the film
# answering an aside can outrank the one answering the diagnosis.
# Headers that label a report's verdict section. When one is present it IS the
# conclusion and the softer cues below are ignored: a hedge in the findings
# ("研判為橫膈上抬…或輕度肺膨脹不全") must not promote an aside to principal.
_CONCLUSION_HEADERS = ("整體印象", "印象", "impression", "診斷")
_CONCLUSION_CUES = _CONCLUSION_HEADERS + ("符合", "考慮", "研判",
                                          "consistent with", "suggestive of", "compatible with")


def _conclusion_clauses(text):
    """Clauses that state the report's conclusion.

    A cue clause and the clause right after it both count: the examiner's
    markdown puts the label and the verdict on either side of a colon
    (「**整體印象：** 右側大量肋膜積水」), and the colon is a clause boundary.
    An explicit impression header wins over the softer cue words.
    """
    cs = _positive_clauses(text)

    def pick(cues):
        picked = []
        for i, c in enumerate(cs):
            if any(k in c.lower() for k in cues):
                picked.append(c)
                if i + 1 < len(cs) and cs[i + 1] not in picked:
                    picked.append(cs[i + 1])
        return picked

    return pick(_CONCLUSION_HEADERS) or pick(_CONCLUSION_CUES) or cs[-1:]


# A clause localizes a lesion only when it is describing ischemic-type change.
# Leads named while describing conduction morphology ("V1 呈 QS 型、V5-V6 寬 R 波"
# in an LBBB report) are not an infarct territory, and reading them as one let a
# film with an unrelated anteroseptal infarct outrank a clean LBBB tracing.
_LESION_CONTEXT_RE = re.compile(
    r"梗塞|缺血|損傷|infarct|isch[ae]*emi|injur"
    r"|st\s*段|st\s*(?:elevation|depression|上升|抬高|壓低|下降)|st-t"
    r"|q\s*波|q\s*wave|t\s*波|t\s*wave|t\s*inversion", re.I)


# A bundle-branch block is complete at QRS >= 120 ms and incomplete below it
# (AHA/ACCF/HRS 2009 recommendations for the standardization of the ECG). Reports
# routinely give the duration without writing 完全性／不完全性, and since every
# LBBB film in the bank is a *complete* block, refusing those reports an image
# (rule 5 for the completeness family) would hide a legitimate teaching film.
_BBB_CUE_RE = re.compile(r"束支|bundle\s*branch|\b[lric]?bbb\b", re.I)
_QRS_MS_RE = re.compile(r"qrs[^0-9]{0,16}(\d{2,3})\s*(?:ms|毫秒|msec)", re.I)


def _implied_completeness(body):
    """Completeness the report implies via the QRS duration, or ``None``.

    Scoped to reports that actually discuss a bundle-branch block: the same
    duration in an infarct report says nothing about a block's completeness.
    """
    if not _BBB_CUE_RE.search(body):
        return None
    m = _QRS_MS_RE.search(body)
    if not m:
        return None
    try:
        return "complete" if int(m.group(1)) >= 120 else "incomplete"
    except ValueError:
        return None


def _report_attributes(report_text) -> dict:
    """Attribute values the *report* asserts (negated clauses ignored)."""
    clauses = _positive_clauses(report_text)
    walls = set()
    for c in clauses:
        if any(k in c.lower() for k in _RECIPROCAL_CUES):
            continue
        if not _LESION_CONTEXT_RE.search(c):
            continue
        walls.update(_territory_of(c))
    body = " ".join(clauses)
    return {"territory": frozenset(walls),
            "completeness": _first_value(body, _COMPLETENESS) or _implied_completeness(body),
            "lesion": _first_value(body, _LESION)}


def _specific_and_umbrella(entry):
    """Split an entry's vocabulary into what the film *claims* (shown in the
    caption) and the broader terms that only widen recall.

    Legacy manifests packed both into ``findings``; there the first item is the
    film's own statement and the rest are the umbrella terms the builder added.
    """
    findings = list(entry.get("findings") or [])
    umbrella = list(entry.get("umbrella_terms") or [])
    return (findings, umbrella) if umbrella else (findings[:1], findings[1:])


def validated_co_findings(entry) -> list:
    """Other findings on the film that a human actually labelled.

    PTB-XL records carry several cardiologist statements per tracing and 71% of
    the bank's ECGs have at least one besides the one the caption names — an
    inferior-infarct film may also show an anteroseptal infarct and a
    fascicular block. Those are as authoritative as the caption and belong in
    the film's *claim*; a report generator must describe them and the student
    must be told they are there. NIH ``co_findings`` are NLP-mined and are NOT
    returned here — they stay informational only.
    """
    if not entry.get("co_findings_validated"):
        return []
    return [c for c in (entry.get("co_findings") or []) if c]


def claim_terms(entry) -> list:
    """Everything the film asserts: its own statement plus validated co-findings.

    This is the term list the attribute gates (territory, lesion, completeness)
    and the purity ranking are computed from, and the list a fixed or generated
    report has to cover.
    """
    return _specific_and_umbrella(entry)[0] + validated_co_findings(entry)


def _entry_attributes(entry) -> dict:
    """Attribute values the *film* asserts. Prefers the values the builder
    computed; falls back to deriving them so a legacy manifest still gates."""
    stored = entry.get("attributes")
    if isinstance(stored, dict):
        return {"territory": frozenset(stored.get("territory") or ()),
                "completeness": stored.get("completeness"),
                "lesion": stored.get("lesion"),
                "laterality": stored.get("laterality")}
    # Derived through the same helper the builders call, so a legacy entry and a
    # freshly built one can never be gated differently. ``laterality`` stays None:
    # a dataset label that omits the side (NIH) is annotated, never guessed.
    derived = claim_attributes(*claim_terms(entry))
    derived["territory"] = frozenset(derived["territory"])
    return derived


def claim_attributes(*claim_terms) -> dict:
    """Attribute values a finding statement asserts, in manifest (JSON) shape.

    Builders call this so what a manifest stores is computed by the very
    vocabulary the retrieval gate enforces — the two can never drift apart.
    ``laterality`` is always ``None``: no open dataset labels the side, so it is
    left for a reviewer to fill in rather than guessed at.
    """
    terms = [str(t) for t in claim_terms if t]
    own = terms[0] if terms else ""
    # A wall name only localizes a *lesion*. "left anterior fascicular block" and
    # "septal hypertrophy" name a conduction path and an anatomical structure, not
    # an infarct territory; counting those as localized would hide them from every
    # report that does not happen to name a wall (rule R5). Territory is the
    # union over every lesion statement on the film (the student sees all of
    # them), but completeness and lesion type are properties of ONE statement —
    # a co-labelled "incomplete right bundle branch block" does not make the
    # film's third-degree AV block incomplete — so they come from the film's own
    # statement only.
    walls = set()
    for t in terms:
        if _first_value(t, _LESION):
            walls.update(_territory_of(t))
    return {"territory": sorted(walls),
            "completeness": _first_value(own, _COMPLETENESS),
            "lesion": _first_value(own, _LESION),
            "laterality": None}


def _reported_laterality(terms, report_text):
    """Side named in the clause(s) that actually evidence ``terms`` — scoped, so
    a second finding's side elsewhere in the report cannot veto this film."""
    hits = [c for c in _positive_clauses(report_text) if _evidence_count(terms, c)]
    return _sole_value(" ".join(hits), _LATERALITY) if hits else None


def _contradicts(entry_attrs, report_attrs, claim_terms, report_text) -> bool:
    """True when showing this film would assert something the report did not."""
    e_terr, r_terr = entry_attrs["territory"], report_attrs["territory"]
    if e_terr and r_terr and not (e_terr & r_terr):
        return True                     # different wall entirely
    if e_terr and not r_terr:
        return True                     # film localizes, report never did
    for family in ("completeness", "lesion"):
        e_val, r_val = entry_attrs[family], report_attrs[family]
        if e_val and r_val and e_val != r_val:
            return True
        if e_val and not r_val:
            # Rule 5 again, for the scalar families: a film labelled
            # "incomplete right bundle branch block" must not stand in for a
            # report that only wrote 「右束支傳導阻滯」 — otherwise the
            # demographic tiebreak decides complete vs incomplete, which is a
            # different reading. Prefer text-only (or a film that claims
            # nothing) over asserting the degree for the examiner.
            return True
    side = entry_attrs["laterality"]
    if side:
        reported = _reported_laterality(claim_terms, report_text)
        if reported and reported != side:
            return True
    return False


def _longest_match(terms, text) -> int:
    """Length of the most specific surface form of ``terms`` the report used.

    Clinical names nest: 「室上性心搏過速」 is a substring of 「陣發性室上性心搏過速」,
    「T波異常」 of 「非診斷性T波異常」. Both films then match, so the tie is broken
    by how fully the report spelled the film's own name — the report that wrote
    the longer term meant the more specific film.
    """
    clauses = [_norm(c) for c in _positive_clauses(text)]
    if not clauses:
        return 0
    best = 0
    for t in terms:
        if not t:
            continue
        for form in (gn.surface_forms(t) or [t]):
            nf = _norm(form)
            if _form_long_enough(nf) and any(nf in c for c in clauses):
                best = max(best, len(nf))
    return best


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


def _norm_sex(s):
    if not s:
        return None
    s = str(s).strip().upper()
    if s.startswith("M") or s.startswith("男") or s == "0":
        return "M"
    if s.startswith("F") or s.startswith("W") or s.startswith("女") or s == "1":
        return "F"
    return None


_SEX_PENALTY = 40.0        # a sex mismatch ~ 40 "years" of age distance
_MISSING_DEMO_PENALTY = 20.0


def _demo_distance(entry, patient_age, patient_sex):
    """Secondary ranking distance between an entry and the virtual patient.

    Purely a *tiebreak* over candidates that already cleared every safety gate —
    it can reorder equally-safe films toward a demographically-closer one but can
    never make an unsafe film eligible. Returns 0 for all entries when no patient
    demographics are supplied, so behaviour is unchanged in that case.
    """
    if patient_age is None and patient_sex is None:
        return 0.0
    d = 0.0
    ps = _norm_sex(patient_sex)
    if ps:
        es = _norm_sex(entry.get("sex"))
        if es is None:
            d += _MISSING_DEMO_PENALTY
        elif es != ps:
            d += _SEX_PENALTY
    if patient_age is not None:
        try:
            d += abs(float(entry.get("age")) - float(patient_age))
        except (TypeError, ValueError):
            d += _MISSING_DEMO_PENALTY
    return d


def find_image(modality, has_abnormal, *, report_text=None, item_terms=None,
               patient_age=None, patient_sex=None, directory=None, manifest=None):
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
        patient_age, patient_sex: the virtual patient's demographics; used ONLY
            as a tiebreak among already-safe candidates (prefer a closer age/sex).
    """
    if not modality or not enabled():
        return None
    directory = directory or bank_dir()
    man = manifest if manifest is not None else load_manifest(directory)
    want = "abnormal" if has_abnormal else "normal"
    item_terms = [t for t in (item_terms or []) if t]

    report_attrs = _report_attributes(report_text) if want == "abnormal" else None
    conclusion = " ".join(_conclusion_clauses(report_text)) if want == "abnormal" else ""
    conclusion_terr = _territory_of(conclusion)

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
        # The readers looked for the labelled finding and could not see it:
        # showing the film "as an example of" that finding would mislead.
        if want == "abnormal" and target_visibility(e) == "no":
            continue

        if want == "abnormal":
            claim, umbrella = _specific_and_umbrella(e)
            attrs = _entry_attributes(e)
            # The film must not assert a territory/side/severity the report
            # contradicted — or never claimed at all.
            if _contradicts(attrs, report_attrs, claim, report_text):
                continue
            # The film's OWN documented finding must be evidenced in this report,
            # so we only ever show a film of the pathology this exam described.
            claim_score = _evidence_count(claim, report_text)
            umbrella_score = _evidence_count(umbrella, report_text)
            score = claim_score + umbrella_score
            if score == 0:
                continue
            # An umbrella term alone is too coarse to pick between films; it
            # only counts as specific when the localization also agrees.
            specific = bool(claim_score) or bool(
                umbrella_score and attrs["territory"] and report_attrs["territory"]
                and (attrs["territory"] & report_attrs["territory"]))
            # Does this film answer the report's conclusion, or an aside?
            principal = bool(_evidence_count(claim + umbrella, conclusion)) or bool(
                attrs["territory"] and (attrs["territory"] & conclusion_terr))
            # 0 = the film's territory is exactly the reported one, 1 = it
            # overlaps, 2 = the film has none (nothing to get wrong).
            if report_attrs["territory"] and attrs["territory"] == report_attrs["territory"]:
                exactness = 0
            else:
                exactness = 1 if attrs["territory"] else 2
            precision = _longest_match(claim, report_text)
        else:
            # A normal single-region film is safe as-is; a multi-anatomy normal
            # film must match the ordered study's region or we don't show it.
            if modality in SINGLE_REGION_MODALITIES:
                score = 1
            else:
                score = _region_score(e, item_terms)
                if score == 0:
                    continue
            specific, principal, exactness, precision = True, True, 2, 0
        # Validated co-findings the report did NOT mention are things the
        # student will see on the film without being told: the fewer, the
        # cleaner the example. (Counted only for human-labelled co-findings —
        # NIH's NLP co-labels are noise, not knowledge.)
        unmentioned = sum(1 for c in validated_co_findings(e)
                          if not _evidence_count([c], report_text))
        scored.append((specific, principal, exactness, precision, score, unmentioned, e))

    if not scored:
        return None
    # A film answering the conclusion beats one answering an aside; a specific
    # match beats a merely-umbrella one. Both are filters, not weights, so
    # demographics can never promote a clinically worse film.
    if any(c[1] for c in scored):
        scored = [c for c in scored if c[1]]
    if any(c[0] for c in scored):
        scored = [c for c in scored if c[0]]
    # Then: closest territory, fewest unmentioned validated co-findings, most
    # evidenced terms, and — only once those tie — the film whose own name the
    # report spelled out most fully (comparing form length is meaningful for
    # nesting names, arbitrary otherwise, so it must stay below the evidence
    # count). Demographics remain the last real tiebreak, then the id.
    # A film whose finding the readers saw clearly outranks one they found
    # equivocal — that is a property of the film, so it sits right after the
    # territory match and ahead of every report-side tiebreak.
    scored.sort(key=lambda c: (c[2], _VISIBILITY_RANK.get(target_visibility(c[6]), 1),
                               c[5], -c[4], -c[3],
                               _demo_distance(c[6], patient_age, patient_sex),
                               str(c[6].get("id", ""))))
    return scored[0][6]


# Findings whose reading depends on which side they are on. No open chest
# dataset records the side, so until a reviewer annotates it the student has to
# be told the film may show the other one.
SIDED_FINDINGS = ("pneumothorax", "pleural effusion", "atelectasis", "consolidation",
                  "pneumonia", "pulmonary mass", "pulmonary nodule", "pulmonary infiltrate",
                  "pleural thickening", "diaphragmatic hernia")

_WALL_ZH = {"anterior": "前壁", "septal": "中隔", "lateral": "側壁",
            "inferior": "下壁", "posterior": "後壁"}
_SIDE_ZH = {"right": "右側", "left": "左側", "bilateral": "兩側"}


def has_sided_finding(entry) -> bool:
    blob = " ".join(entry.get("findings") or []).lower()
    return any(f in blob for f in SIDED_FINDINGS)


def _walls_zh(walls):
    return "＋".join(_WALL_ZH.get(w, w) for w in sorted(walls))


# Readers dutifully list the radiographic side marker, the technologist's
# initials and de-identification boxes under "devices". They are on the film,
# not in the patient, so the student is not told about them.
NOT_A_DEVICE_RE = re.compile(
    r"marker|initials?|technologist|technician|artifact|artefact|"
    r"側別|側標記|鉛字|技師|技術員|放射師|標記|偽影|遮蔽框|去識別", re.I)


def device_names(entry) -> list:
    """Distinct zh-TW device names on the film, in annotation order.

    Readers list every electrode and every wire separately; the student needs
    「心電圖監測電極」 once, not six times, so names are de-duplicated on the part
    before any parenthetical location. Side markers and other things that are
    on the film rather than in the patient are dropped (NOT_A_DEVICE_RE).
    """
    ann = entry.get("annotation") if isinstance(entry.get("annotation"), dict) else {}
    seen, out = set(), []
    for d in ann.get("devices") or []:
        if not isinstance(d, dict):
            continue
        name = (d.get("zh") or d.get("type") or "").strip()
        if NOT_A_DEVICE_RE.search(name) or NOT_A_DEVICE_RE.search(d.get("type") or ""):
            continue
        key = re.split(r"[（(]", name)[0].strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(re.split(r"[（(]", name)[0].strip())
    return out


def _finding_zh(term) -> str:
    """The longest CJK surface form of a finding, for display; the term itself
    when its synonym group has none."""
    cjk = [f for f in gn.surface_forms(term) if re.search(r"[\u4e00-\u9fff]", f)]
    return max(cjk, key=len) if cjk else term


def _who(sex, age):
    bits = []
    if sex:
        bits.append({"M": "男性", "F": "女性"}.get(sex, sex))
    if age is not None:
        try:
            bits.append("%d 歲" % int(float(age)))
        except (TypeError, ValueError):
            pass
    return " ".join(bits) or "未知人口學"


# How much anyone has actually vouched for a film's *label*. ``verified`` only
# controls whether the film may be shown; it says nothing about who checked it,
# and the two must not be conflated:
#
#   clinician              a teacher here opened the file and confirmed it
#   cardiologist-validated PTB-XL: cardiologist-annotated + validated_by_human
#   radiologist-bbox       NIH: a radiologist drew a box FOR the existing label.
#                          That is localization, not adjudication — one reader,
#                          no second review, anchored on a label they were given,
#                          and the dataset records no case where a reader
#                          rejected a label. Stronger than NLP alone; not proof.
#   nlp-mined              text-mined from the report, ~90% accurate, unchecked
_TRUSTED_LABELS = ("clinician", "cardiologist-validated")


def target_visibility(entry) -> str:
    """What the three-reader review concluded about the film's OWN labelled finding.

    ``"yes"`` — seen; ``"uncertain"`` — equivocal; ``"no"`` — the adjudicator
    could not see it on this film; ``""`` — not reviewed. A clinician's sign-off
    (``clinician_reviewed``) outranks the AI readers and reads as ``"yes"``.
    """
    if not entry:
        return ""
    if entry.get("clinician_reviewed"):
        return "yes"
    ann = entry.get("annotation") if isinstance(entry.get("annotation"), dict) else {}
    tgt = ann.get("target") if isinstance(ann.get("target"), dict) else {}
    vis = str(tgt.get("visible") or "").strip().lower()
    return vis if vis in ("yes", "uncertain", "no") else ""


_VISIBILITY_RANK = {"yes": 0, "": 1, "uncertain": 2}


def review_status(entry) -> str:
    """Who has vouched for this film's label — see ``_TRUSTED_LABELS`` above."""
    if not entry:
        return "unknown"
    if entry.get("clinician_reviewed"):
        return "clinician"
    return entry.get("label_verification") or "unknown"


def risk_notes(entry, *, report_text=None, patient_age=None, patient_sex=None) -> list:
    """The ways this real film may still not match the virtual case.

    Every gate in this module removes images that would *mislead*. What is left
    over is the mismatch inherent to showing one real patient's film as an
    example of another (simulated) patient's finding — different person,
    different demographics, possibly a different side or a wider territory, plus
    whatever else happens to be on the original film. A student reading it as a
    diagnostic image has to see those differences stated, not infer them.

    Returns display-ready lines, most film-specific first; the caller renders
    them under the image.
    """
    if not entry:
        return []
    if entry.get("synthetic"):
        return ["本圖為 AI 生成的外觀示意圖，**非真實影像**，不可作為判讀練習的依據。"]

    notes = ["本圖是**另一位真實病人**的檢查影像，不是這位虛擬病人的片子，"
             "只用來示範報告中提到的那個所見。"]

    # Who vouched for the label goes second: it is what decides how much weight
    # the student may put on the image at all.
    status = review_status(entry)
    if status == "nlp-mined":
        notes.append("🔴 **本圖的所見標籤未經任何醫師確認。** 它是程式從原始放射科報告的文字"
                     "自動探勘出來的，原始論文自述準確率約 90%——**平均每 10 張就有約 1 張標錯**，"
                     "而且尚未有臨床教師覆核。請把它當作「可能是這個所見」的參考圖，"
                     "**不要當成標準答案，也不要用它來校正你的判讀**。")
    elif status == "radiologist-bbox":
        notes.append("🟠 **本圖的標籤未經獨立覆核。** NIH 的放射科專科醫師曾在這張片上為此所見"
                     "畫出定位框，但那是「在既有標籤上標出位置」，**不是對標籤本身的驗證**："
                     "單一判讀者、無第二人複核、且判讀時已知標籤，而標籤本身同樣來自文字探勘。"
                     "比純文字探勘可信，但仍未經臨床教師覆核。")
    elif status == "cardiologist-validated":
        notes.append("本圖的所見為原始資料集的**心臟科醫師標註並經人工複核**，"
                     "但尚未經本課程的臨床教師再次確認。")
    elif status != "clinician":
        notes.append("🟠 **本圖標籤的來源未記錄**，無法判斷是否經過醫師確認，請謹慎採用。")

    attrs = _entry_attributes(entry)
    reported = _report_attributes(report_text) if report_text else {"territory": frozenset()}

    # side — the largest single risk while the bank is only partly annotated
    if has_sided_finding(entry):
        side = attrs.get("laterality")
        stored = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
        if not side:
            notes.append("本圖的病灶**位於左側還是右側尚未經人工標註**，"
                         "有可能與報告描述的側別相反 —— **請以文字報告的側別為準**。")
        elif side in _SIDE_ZH and stored.get("laterality_reviewed"):
            notes.append("本圖的病灶位於 **%s**（經審閱者確認）。" % _SIDE_ZH[side])
        elif side in _SIDE_ZH:
            # provisional: say which sources actually agreed. Claiming a
            # radiologist's box contributed when only the AI readers did would
            # overstate the evidence on exactly the axis that matters most.
            if stored.get("laterality_source") == "bbox+ai-vision":
                notes.append("本圖的病灶位於 **%s** —— 此側別由放射科醫師定位框的位置與 AI 讀片"
                             "一致推得，**尚未經放射科醫師確認**。" % _SIDE_ZH[side])
            else:
                notes.append("本圖的病灶位於 **%s** —— 此側別**僅由 AI 讀片推得**"
                             "（本圖沒有放射科醫師定位框），未經任何醫師確認，"
                             "**請以文字報告的側別為準**。" % _SIDE_ZH[side])

    # territory — overlapping but not identical passed the gate on purpose
    e_walls, r_walls = attrs["territory"], reported["territory"]
    if e_walls and r_walls and e_walls != r_walls:
        notes.append("本圖的心肌定位是 **%s**，報告描述的是 **%s**——"
                     "兩者有重疊但**不完全相同**，範圍請以報告為準。"
                     % (_walls_zh(e_walls), _walls_zh(r_walls)))

    # demographics — the ranking prefers a close match but cannot guarantee one
    e_sex, p_sex = _norm_sex(entry.get("sex")), _norm_sex(patient_sex)
    e_age, p_age = entry.get("age"), patient_age
    far = e_sex and p_sex and e_sex != p_sex
    try:
        far = far or (e_age is not None and p_age is not None
                      and abs(float(e_age) - float(p_age)) >= 10)
    except (TypeError, ValueError):
        pass
    if far:
        baseline = {"ECG": "QRS 電壓、幼年型 T 波倒置、心率與 QTc 的正常範圍",
                    "CXR": "心胸比標準、胸廓形態、幼童可見的胸腺影"}.get(
            entry.get("modality"), "該檢查的正常值基準")
        notes.append("影像來自 **%s**，本病人是 **%s**。年齡與性別會改變 %s，"
                     "比對時請留意。"
                     % (_who(e_sex, e_age), _who(p_sex, p_age), baseline))

    if str(entry.get("view_position") or "").upper() == "AP":
        notes.append("本圖為 **AP（前後位）** 攝影，心影會被放大，"
                     "不可直接套用 PA 片的心胸比標準。")

    ann = entry.get("annotation") if isinstance(entry.get("annotation"), dict) else {}

    # What the three-reader review made of the label itself. An equivocal
    # target is the single most important thing to tell the student, and the
    # readers' reservation is quoted rather than summarised.
    vis = target_visibility(entry)
    concern = str(ann.get("label_concern") or "").strip()
    if vis == "uncertain":
        notes.append("🔴 **三位 AI 讀片員無法確認本片確有「%s」**——只看到可疑或不典型的變化。"
                     "請把它當作「可能」的例子，判讀以文字報告為準。%s"
                     % ("、".join(_finding_zh(f) for f in (entry.get("findings") or [])) or "此所見",
                        ("讀片備註：%s" % concern) if concern else ""))
    elif concern and status != "clinician":
        notes.append("讀片備註（AI 三讀仲裁，非放射科醫師）：%s" % concern)
    if ann.get("treatment_evidence"):
        notes.append("本片上**已有治療痕跡**（%s）——這是治療後的追蹤影像，"
                     "不是初次就診時的表現，嚴重度會比報告描述的輕。"
                     % ("、".join(device_names(entry)) or "見讀片描述"))
    # PTB-XL: the translation pass compared the cardiologist's words with the
    # coded statements and the structured fields; a recorded disagreement
    # (record 4166: stadium "acute" vs "probably old") is quoted.
    issues = str(ann.get("report_issues") or "").strip()
    if issues and ann.get("report_consistent_with_codes") is False:
        notes.append("原始判讀與資料集的標註代碼**有出入**（核對備註：%s）——"
                     "本報告以心臟科醫師的判讀文字為準。" % issues)
    # The structured infarct stadium can contradict the cardiologist's own words
    # (record 4166: "Stadium I" vs "wahrscheinlich alt"); the words win.
    stadium, words = str(ann.get("stadium_zh") or ""), str(ann.get("source_report_zh") or "")
    if stadium and words and (
            ("急性" in stadium and "陳舊" not in stadium and re.search(r"陳舊|舊性", words))
            or ("陳舊" in stadium and "急性" not in stadium and re.search(r"急性|新近", words))):
        notes.append("資料集的梗塞分期欄位標為**%s**，但心臟科醫師的判讀文字寫的是「%s」——"
                     "以判讀文字為準，分期欄位待人工複核。" % (stadium, words))

    # Human-labelled co-findings (PTB-XL): they ARE on the tracing. Say which,
    # and say whether this report covered them.
    validated = validated_co_findings(entry)
    if validated:
        zh = {s.get("description"): s.get("zh") for s in (ann.get("statements") or [])
              if isinstance(s, dict)}
        names = "、".join(zh.get(c) or c for c in validated)
        missing = [zh.get(c) or c for c in validated
                   if not _evidence_count([c], report_text)]
        if missing:
            notes.append("本圖經心臟科醫師標註**另有** **%s**，而本次報告未提到——"
                         "圖上這些變化屬於原病人，判讀時請勿套用到本虛擬病人。" % "、".join(missing))
        else:
            notes.append("本圖經心臟科醫師標註另有 **%s**，報告已一併描述。" % names)

    # Devices seen by the readers (CXR sidecar): a chest tube or a port is
    # exactly the kind of thing a first-presentation case must not contradict.
    devices = device_names(entry)
    if devices:
        notes.append("影像上可見**%s**——這些屬於原病人的治療或裝置，"
                     "除非報告特別說明，否則與本虛擬病人無關。" % "、".join(devices))

    co = [c for c in (entry.get("co_findings") or []) if c] if not validated else []
    if co:
        notes.append("原始資料集另外標記本圖可能還有 **%s**（僅由報告文字探勘取得、"
                     "未經放射科醫師確認）；這些**不代表本虛擬病人也有**。" % "、".join(co))
    elif not validated and not devices:
        notes.append("真實影像上可能還有報告沒提到的其他變化（舊病灶、管路、術後改變等），"
                     "那些屬於原本那位病人，與本虛擬病人無關。")

    measures = {"ECG": "ST 段抬升幅度、QRS 電壓、心率與各間期",
                "CXR": "病灶大小、心胸比、積水量、氣胸塌陷程度"}.get(
        entry.get("modality"), "病灶大小與各項量測值")
    notes.append("**嚴重程度與量測值不保證一致**——%s，請一律以文字報告為準，"
                 "不要從圖上反推。" % measures)

    return notes


def expected_normality(text) -> bool:
    """``has_abnormal`` for a fixed/expected report: the examiner's own trailing
    [NORMAL]/[ABNORMAL] tag when present, else abnormal (the conservative side)."""
    upper = str(text or "").upper()
    if "[NORMAL]" in upper and "[ABNORMAL]" not in upper:
        return False
    return True


def preselect_for_case(modality, expected_text, *, patient_age=None, patient_sex=None,
                       item_terms=None, directory=None, manifest=None):
    """Pick the film a report should be written FROM, before the report exists.

    The examination page calls this when a case carries an explicit expected
    finding (``Problem.檢查預期所見``): the film is chosen first, its annotation is
    handed to the report writer, and the report therefore describes *this* film
    — the inverse of matching a film to a finished report, and the only way the
    two can never disagree.

    Stricter than :func:`find_image` for abnormal results: the film's own claim
    must be evidenced in the expected text. An umbrella-only match would let a
    vague hint ("myocardial infarction") pull a wall-specific film and then have
    the report written to it, which is the anchoring the gates exist to stop.
    """
    if not expected_text:
        return None
    has_abnormal = expected_normality(expected_text)
    hit = find_image(modality, has_abnormal, report_text=expected_text, item_terms=item_terms,
                     patient_age=patient_age, patient_sex=patient_sex,
                     directory=directory, manifest=manifest)
    if hit is None:
        return None
    if has_abnormal and not _evidence_count(_specific_and_umbrella(hit)[0], expected_text):
        return None
    return hit


def annotation_brief_zh(entry) -> str:
    """What is actually on this film, as text a report writer can be held to.

    Built from the per-image annotation (PTB-XL statements + cardiologist
    report for ECGs; the three-reader description, devices and side/zone for
    chest films) so a generated report can describe the real film instead of
    the textbook. Empty string when the entry carries no annotation.
    """
    if not entry:
        return ""
    ann = entry.get("annotation") if isinstance(entry.get("annotation"), dict) else {}
    lines = []
    claim = _specific_and_umbrella(entry)[0]
    stmts = [s for s in (ann.get("statements") or []) if isinstance(s, dict)]
    zh_of = {s.get("description"): s.get("zh") for s in stmts}
    main = "、".join(zh_of.get(c) or c for c in claim)
    if main:
        lines.append("主要所見：%s" % main)
    others = validated_co_findings(entry)
    if others:
        lines.append("同一張影像上另有（人工標註）：%s" % "、".join(zh_of.get(c) or c for c in others))
    if entry.get("modality") == "ECG":
        if ann.get("axis_zh"):
            lines.append("電軸：%s" % ann["axis_zh"])
        if ann.get("stadium_zh"):
            # PTB-XL's structured stadium can disagree with the cardiologist's
            # own words (record 4166: "Stadium I" but "wahrscheinlich alt"), so
            # it is labelled as the structured field and the free text follows.
            lines.append("梗塞分期（結構化欄位）：%s" % ann["stadium_zh"])
        # PTB-XL gives rhythm/form statements no likelihood (0.0 = not scored,
        # not absent), so they are listed whenever they are on the record.
        rhythm = [s.get("zh") or s.get("description") for s in stmts
                  if s.get("kind") == "rhythm" and s.get("description") not in claim]
        if rhythm:
            lines.append("節律：%s" % "、".join(r for r in rhythm if r))
        form = [s.get("zh") or s.get("description") for s in stmts
                if s.get("kind") == "form" and s.get("description") not in claim
                and s.get("description") not in others]
        if form:
            lines.append("型態描述：%s" % "、".join(f for f in form if f))
        if ann.get("source_report_zh"):
            lines.append("原始心臟科判讀：%s" % ann["source_report_zh"])
        elif ann.get("source_report"):
            # untranslated (German / Swedish / English shorthand) — still the
            # cardiologist's actual reading, so it is shown rather than dropped
            lines.append("原始心臟科判讀（原文，未翻譯）：%s" % ann["source_report"].strip())
        if ann.get("pacemaker"):
            lines.append("節律器：%s" % ann["pacemaker"])
        if ann.get("extra_beats"):
            lines.append("期外收縮：%s" % ann["extra_beats"])
        if ann.get("report_issues"):
            # the translator's caveats (a stadium field that contradicts the
            # cardiologist's words, an abbreviation read a particular way)
            lines.append("判讀核對備註：%s" % ann["report_issues"])
    else:
        tgt = ann.get("target") if isinstance(ann.get("target"), dict) else {}
        loc = []
        if tgt.get("laterality") in _SIDE_ZH:
            loc.append(_SIDE_ZH[tgt["laterality"]])
        zone_zh = {"upper": "上肺野", "middle": "中肺野", "lower": "下肺野", "whole": "整側",
                   "multiple": "多處"}.get(tgt.get("zone"))
        if zone_zh:
            loc.append(zone_zh)
        try:
            if float(tgt.get("size_cm", -1)) > 0:
                loc.append("約 %.0f 公分" % float(tgt["size_cm"]))
        except (TypeError, ValueError):
            pass
        if loc:
            lines.append("病灶位置：%s" % "、".join(loc))
        if entry.get("view_position"):
            lines.append("攝影體位：%s" % entry["view_position"])
        devices = device_names(entry)
        if devices:
            lines.append("影像上的裝置／管線：%s" % "、".join(devices))
        extra = [o for o in (ann.get("other_findings") or []) if o]
        if extra:
            lines.append("其他可見變化：%s" % "、".join(extra))
        if ann.get("description_zh"):
            lines.append("讀片描述：%s" % ann["description_zh"])
        vis_zh = {"yes": "清楚可見", "uncertain": "不確定（只見可疑或不典型變化）",
                  "no": "看不到"}.get(target_visibility(entry))
        if vis_zh:
            lines.append("讀片員對標籤所見的能見度：%s" % vis_zh)
        if ann.get("label_concern"):
            lines.append("標籤疑慮：%s" % ann["label_concern"])
        if ann.get("treatment_evidence"):
            lines.append("治療痕跡：有（治療後的追蹤影像，不是初次就診的表現）")
    return "\n".join("- " + ln for ln in lines)


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
