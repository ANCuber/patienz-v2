"""Clinical-term normalization for grading transparency.

The LLM grader does the actual scoring; this module (a) gives the post-grading
**詳解 / model-answer panel** a way to show "your term ≈ the standard term" so a
student who wrote ``DMARD`` / ``x光攝影`` / ``肺栓塞`` can see they matched the
standard ``DMARDs`` / ``x-ray`` / ``PE`` — addressing the repeated feedback that
"差一個 s 就不給分" — and (b) documents the canonical vocabulary the grader
prompt is told to honor.

These are *display/aid* helpers, intentionally conservative: they reduce false
mismatches but never invent a match the grader didn't make.
"""
import re

# canonical concept -> set of accepted surface forms (compared after _key()).
# Keep curated and high-precision; the grader prompt handles the long tail.
_SYNONYM_GROUPS = {
    "nsaid": {"nsaid", "nsaids", "非類固醇消炎止痛藥", "非類固醇抗發炎藥", "非固醇類消炎藥"},
    "dmard": {"dmard", "dmards", "疾病修飾抗風濕藥", "疾病修飾抗風溼藥物", "疾病修飾抗風濕藥物"},
    "x-ray": {"xray", "x-ray", "x ray", "x光", "x光攝影", "radiograph", "plainfilm", "放射線攝影"},
    "ct": {"ct", "computedtomography", "電腦斷層", "電腦斷層攝影"},
    "mri": {"mri", "magneticresonanceimaging", "磁振造影", "核磁共振"},
    "ecg": {"ecg", "ekg", "electrocardiogram", "心電圖"},
    "pe-pulmonary-embolism": {"pe", "pulmonaryembolism", "肺栓塞", "肺動脈栓塞"},
    "ami": {"ami", "mi", "myocardialinfarction", "心肌梗塞", "急性心肌梗塞"},
    "cbc": {"cbc", "cbcdc", "completebloodcount", "全血球計數", "血液檢驗", "血常規"},
    "rf": {"rf", "rheumatoidfactor", "類風濕因子", "類風溼因子"},
    "anti-ccp": {"anticcp", "ccp", "抗環瓜氨酸抗體"},
    "occult-blood": {"stoolob", "occultblood", "fobt", "糞便潛血", "潛血"},
    "ppi": {"ppi", "protonpumpinhibitor", "質子幫浦抑制劑"},
    "creatinine": {"creatinine", "cr", "肌酸酐", "肌酐"},
    "crp": {"crp", "creactiveprotein", "c反應蛋白"},
    # Common imaging-diagnosis concepts. Serve double duty: grading (student
    # writes 肺炎, standard says pneumonia) and §6-A image retrieval (case
    # englishDiseaseName ↔ image bank disease_keywords, 中英 tolerant).
    "pneumonia": {"pneumonia", "肺炎", "肺部感染"},
    "pneumothorax": {"pneumothorax", "氣胸"},
    "pleural-effusion": {"pleuraleffusion", "肋膜積水", "肋膜積液", "胸腔積液", "胸水"},
    "fracture": {"fracture", "骨折"},
    "atrial-fibrillation": {"atrialfibrillation", "afib", "心房顫動", "心房纖維顫動"},
    "stroke": {"stroke", "cva", "cerebrovascularaccident", "中風", "腦中風"},
    "intracranial-hemorrhage": {"intracranialhemorrhage", "intracerebralhemorrhage",
                                "ich", "腦出血", "顱內出血", "腦溢血"},
}

# build reverse lookup: surface key -> canonical concept
_REVERSE = {}
for _concept, _forms in _SYNONYM_GROUPS.items():
    for _f in _forms:
        _REVERSE[_f] = _concept


def _key(term: str) -> str:
    """Lowercase, drop all non-alphanumeric (incl. CJK kept), so spacing /
    punctuation / case do not matter. CJK characters are alphanumeric to
    ``str.isalnum`` so they survive."""
    if not term:
        return ""
    return "".join(ch for ch in term.lower() if ch.isalnum())


def _depluralize(k: str) -> str:
    """Strip a trailing ASCII plural 's' (DMARDs -> dmard). Only applies when the
    stem is ascii to avoid mangling CJK."""
    if len(k) > 2 and k.isascii() and k.endswith("s") and not k.endswith("ss"):
        return k[:-1]
    return k


def canonical(term: str) -> str:
    """Return a canonical concept id for a free-text clinical term.

    Falls back to the depluralized key when no synonym group matches, so that
    ``DMARD``/``DMARDs`` still collapse together even without a table entry.
    """
    k = _key(term)
    if not k:
        return ""
    if k in _REVERSE:
        return _REVERSE[k]
    dk = _depluralize(k)
    if dk in _REVERSE:
        return _REVERSE[dk]
    return dk


def surface_forms(term: str):
    """Accepted surface forms for a term's concept (its synonym group), or
    ``[term]`` when it maps to no group.

    Used by the §6-A image bank to look a curated finding (e.g. an image's
    ground-truth ``氣胸`` / ``pneumothorax``) up inside a free-text examination
    report — the *controlled-vocabulary-into-narrative* direction, which is safe
    from the substring false-positives that a term-vs-term compare would have.
    """
    c = canonical(term)
    grp = _SYNONYM_GROUPS.get(c)
    if grp:
        return sorted(grp)
    return [term] if term else []


def terms_match(a: str, b: str) -> bool:
    """True if two free-text terms refer to the same concept (synonym/plural/
    spacing/case/中英 tolerant)."""
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # Substring tolerance for compound phrases (e.g. "order chest x-ray" vs
    # "x-ray"), but only for sufficiently long keys — otherwise 2-letter
    # abbreviations create false positives (e.g. "rf" ⊂ "crf", "ct" ⊂ "ect").
    if len(ca) < 3 or len(cb) < 3:
        return False
    return ca in cb or cb in ca


def _split_terms(text):
    """Split a free-text answer into candidate terms on common delimiters."""
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        parts = []
        for t in text:
            parts.extend(_split_terms(t))
        return parts
    parts = re.split(r"[、,;/，；和及與\n]+", str(text))
    return [p.strip() for p in parts if p.strip()]


def match_against_standard(student_text, standard_terms):
    """For each standard term, report whether the student covered it.

    Returns a list of ``{"standard": term, "covered": bool, "matched_by": str}``
    used by the 詳解 panel to make "did I get credit?" transparent.
    """
    student_terms = _split_terms(student_text)
    out = []
    for std in _split_terms(standard_terms):
        matched_by = ""
        for stu in student_terms:
            if terms_match(stu, std):
                matched_by = stu
                break
        out.append({"standard": std, "covered": bool(matched_by), "matched_by": matched_by})
    return out
