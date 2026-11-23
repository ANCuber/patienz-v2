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
    "ami": {"ami", "mi", "myocardialinfarction", "心肌梗塞", "急性心肌梗塞",
            # STEMI/NSTEMI are how an ECG report actually names an infarct; without
            # them a report reading "acute inferior STEMI" evidences nothing.
            "stemi", "nstemi", "stelevationmyocardialinfarction",
            "nonstelevationmyocardialinfarction", "st段上升型心肌梗塞",
            "非st段上升型心肌梗塞", "st上升型心肌梗塞"},
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
    # ECG-diagnosis concepts (§6-A PTB-XL image bank). Same double duty: they let
    # a Chinese examiner report ("左束支傳導阻滯"/"心房撲動"…) evidence a real ECG's
    # ground-truth finding, and give the grading 詳解 panel 中英/同義 tolerance.
    "myocardial-ischemia": {"myocardialischemia", "ischemia", "ischemic", "ischaemia",
                            "心肌缺血", "缺血"},
    # Localized infarct/ischemia statements carried by PTB-XL. Their English is
    # the only form the dataset ships, so without a Chinese equivalent a report
    # written as 「下壁心肌梗塞」 can never evidence the film that actually shows
    # it — and the retrieval falls back to whatever generic term did match.
    "inferior-myocardial-infarction": {"inferiormyocardialinfarction", "下壁心肌梗塞",
                                       "下壁梗塞", "下壁stemi"},
    "anterior-myocardial-infarction": {"anteriormyocardialinfarction", "前壁心肌梗塞",
                                       "前壁梗塞", "前壁stemi"},
    "anteroseptal-myocardial-infarction": {"anteroseptalmyocardialinfarction",
                                           "前間壁心肌梗塞", "前中隔心肌梗塞", "前間壁梗塞"},
    "anterolateral-myocardial-infarction": {"anterolateralmyocardialinfarction",
                                            "前側壁心肌梗塞", "前側壁梗塞"},
    "lateral-myocardial-infarction": {"lateralmyocardialinfarction", "側壁心肌梗塞", "側壁梗塞"},
    "inferolateral-myocardial-infarction": {"inferolateralmyocardialinfarction",
                                            "下側壁心肌梗塞", "下側壁梗塞"},
    "posterior-myocardial-infarction": {"posteriormyocardialinfarction", "後壁心肌梗塞",
                                        "後壁梗塞"},
    "inferoposterior-myocardial-infarction": {"inferoposteriormyocardialinfarction",
                                              "下後壁心肌梗塞"},
    "inferoposterolateral-myocardial-infarction": {"inferoposterolateralmyocardialinfarction",
                                                   "下後側壁心肌梗塞"},
    "ischemic-inferior-leads": {"ischemicininferiorleads", "下壁心肌缺血", "下壁缺血"},
    "ischemic-anterior-leads": {"ischemicinanteriorleads", "前壁心肌缺血", "前壁缺血"},
    "ischemic-anteroseptal-leads": {"ischemicinanteroseptalleads", "前間壁心肌缺血", "前間壁缺血"},
    "ischemic-anterolateral-leads": {"ischemicinanterolateralleads", "前側壁心肌缺血", "前側壁缺血"},
    "ischemic-lateral-leads": {"ischemicinlateralleads", "側壁心肌缺血", "側壁缺血"},
    "ischemic-inferolateral-leads": {"ischemicininferolateralleads", "下側壁心肌缺血", "下側壁缺血"},
    "subendocardial-injury": {"subendocardialinjury", "心內膜下損傷", "心內膜下心肌損傷"},
    # PTB-XL names these statements "complete left bundle branch block" /
    # "incomplete right bundle branch block", and those exact strings are what
    # the manifest stores as a film's finding. Without them in a group,
    # surface_forms() returns the English literal alone, the zh-TW report can
    # never evidence the film, and the risk note tells the student the report
    # "未提到" a finding the report is actually teaching.
    "left-bundle-branch-block": {"leftbundlebranchblock", "lbbb", "clbbb",
                                 "completeleftbundlebranchblock", "左束支傳導阻滯",
                                 "左束支阻滯", "完全性左束支傳導阻滯"},
    "right-bundle-branch-block": {"rightbundlebranchblock", "rbbb", "crbbb",
                                  "completerightbundlebranchblock", "右束支傳導阻滯",
                                  "右束支阻滯", "完全性右束支傳導阻滯"},
    # Incomplete block is a different film and a different reading, so it is its
    # own concept. Its zh forms contain the complete group's forms as substrings
    # (不完全性右束支傳導阻滯 ⊃ 右束支傳導阻滯); the completeness attribute gate in
    # util/image_bank.py is what keeps a complete-block film off an incomplete
    # report and vice versa.
    "incomplete-left-bundle-branch-block": {
        "incompleteleftbundlebranchblock", "ilbbb",
        "不完全性左束支傳導阻滯", "不完全性左束支阻滯"},
    "incomplete-right-bundle-branch-block": {
        "incompleterightbundlebranchblock", "irbbb",
        "不完全性右束支傳導阻滯", "不完全性右束支阻滯"},
    "av-block": {"avblock", "atrioventricularblock", "房室傳導阻滯", "房室阻滯", "房室傳導障礙"},
    # Degree matters clinically (1st = watch, 3rd = pacemaker), and the three
    # PTB-XL statements are separate films, so each degree gets its own group.
    "first-degree-av-block": {"firstdegreeavblock", "firstdegreeatrioventricularblock",
                              "一度房室傳導阻滯", "第一度房室傳導阻滯", "一度房室阻滯"},
    "second-degree-av-block": {"seconddegreeavblock", "seconddegreeatrioventricularblock",
                               "二度房室傳導阻滯", "第二度房室傳導阻滯", "二度房室阻滯"},
    "third-degree-av-block": {"thirddegreeavblock", "thirddegreeatrioventricularblock",
                              "completeheartblock", "三度房室傳導阻滯", "第三度房室傳導阻滯",
                              "完全性房室傳導阻滯", "完全房室傳導阻滯", "完全性房室阻滯"},
    "left-ventricular-hypertrophy": {"leftventricularhypertrophy", "lvh", "左心室肥厚", "左室肥厚"},
    "right-ventricular-hypertrophy": {"rightventricularhypertrophy", "rvh", "右心室肥厚", "右室肥厚"},
    "atrial-flutter": {"atrialflutter", "心房撲動", "心房拍動"},
    "sinus-tachycardia": {"sinustachycardia", "竇性心搏過速", "竇性心動過速", "竇速"},
    "sinus-bradycardia": {"sinusbradycardia", "竇性心搏過緩", "竇性心動過緩", "竇緩"},
    "st-elevation": {"stelevation", "stsegmentelevation", "st段上升", "st段抬高", "st上升"},
    "st-depression": {"stdepression", "stsegmentdepression", "st段壓低", "st段下降", "st下降"},
    "premature-ventricular-complex": {"prematureventricularcomplex", "ventricularprematurecomplex",
                                      "pvc", "心室早期收縮", "室性早搏", "心室期外收縮"},
    "atrial-premature-complex": {"atrialprematurecomplex", "pac", "心房早期收縮", "房性早搏",
                                 "心房期外收縮"},
    "wolff-parkinson-white": {"wolffparkinsonwhite", "wpw", "預激症候群", "沃夫巴金森懷特症候群"},
    "long-qt": {"longqt", "longqtinterval", "qt間期延長", "qt延長", "長qt"},
    "paced-rhythm": {"pacedrhythm", "artificialpacemaker", "節律器心律", "心臟節律器", "節律器"},
    "left-atrial-enlargement": {"leftatrialenlargement", "leftatrialoverload", "左心房擴大",
                                "左房擴大", "左心房肥大"},
    "right-atrial-enlargement": {"rightatrialenlargement", "rightatrialoverload", "右心房擴大",
                                 "右房擴大", "右心房肥大"},
    # The remaining PTB-XL statements. Their English is the only wording the
    # dataset ships, so without a Chinese equivalent the film exists in the bank
    # but no zh-TW examiner report can ever evidence it. Each group carries the
    # statement's exact wording (that is the lookup key) plus the terms a report
    # would realistically use.
    #
    # Deliberately absent: PTB-XL's generic "premature complex(es)". Every
    # Chinese form of it (期外收縮 / 早期收縮) is a substring of the *specific*
    # statements 心房早期收縮 / 心室早期收縮, and atrial vs ventricular ectopy look
    # different on the tracing — so a generic film could displace the right one.
    "conduction-lafb": {"leftanteriorfascicularblock", "lafb", "左前分支阻滯",
                        "左前束支阻滯", "左前半支阻滯"},
    "conduction-lpfb": {"leftposteriorfascicularblock", "lpfb", "左後分支阻滯",
                        "左後束支阻滯", "左後半支阻滯"},
    "ivcd": {"nonspecificintraventricularconductiondisturbanceblock", "ivcd",
             "非特異性心室內傳導障礙", "心室內傳導阻滯", "室內傳導阻滯", "心室內傳導障礙"},
    "prolonged-pr": {"prolongedprinterval", "pr間期延長", "pr波間期延長", "pr間隔延長"},
    "supraventricular-tachycardia": {"supraventriculartachycardia", "室上性心搏過速",
                                     "室上性心動過速", "上心室性心搏過速"},
    "paroxysmal-svt": {"paroxysmalsupraventriculartachycardia", "psvt",
                       "陣發性室上性心搏過速", "陣發性室上性心動過速"},
    "supraventricular-arrhythmia": {"supraventriculararrhythmia", "室上性心律不整",
                                    "上心室性心律不整"},
    "sinus-arrhythmia": {"sinusarrhythmia", "竇性心律不整", "竇性心搏不整"},
    "bigeminy": {"bigeminalpatternunknownoriginsvorventricular", "bigeminy",
                 "二聯律", "二聯脈"},
    "trigeminy": {"trigeminalpatternunknownoriginsvorventricular", "trigeminy",
                  "三聯律", "三聯脈"},
    "pathological-q-wave": {"qwavespresent", "qwavepresent", "病理性q波", "異常q波",
                            "q波出現"},
    "abnormal-qrs": {"abnormalqrs", "qrs波異常", "異常qrs波", "qrs形態異常"},
    "high-qrs-voltage": {"highqrsvoltage", "qrs電壓過高", "qrs高電壓", "高電壓qrs"},
    "low-qrs-voltage": {"lowqrsvoltagesinthefrontalandhorizontalleads", "lowqrsvoltage",
                        "qrs低電壓", "qrs電壓過低", "低電壓qrs"},
    "t-wave-abnormality": {"twaveabnormality", "t波異常"},
    "inverted-t-wave": {"invertedtwaves", "invertedtwave", "t波倒置", "倒置t波"},
    "low-amplitude-t-wave": {"lowamplitudetwaves", "lowamplitudetwave", "t波振幅低下",
                             "低振幅t波", "t波低平"},
    "non-diagnostic-t": {"nondiagnostictabnormalities", "非診斷性t波異常",
                         "非診斷性t波變化"},
    "non-specific-t-change": {"nonspecifictwavechanges", "非特異性t波變化",
                              "非特異性t波改變"},
    "non-specific-st-change": {"nonspecificstchanges", "非特異性st變化",
                               "非特異性st段變化", "非特異性st段改變"},
    "septal-hypertrophy": {"septalhypertrophy", "中隔肥厚", "心室中隔肥厚", "室間隔肥厚"},
    "digitalis-effect": {"digitaliseffect", "毛地黃效應", "洋地黃效應", "毛地黃作用"},
    "electrolyte-or-drug-effect": {"electrolyticdisturbanceordrugformeredis",
                                   "電解質異常或藥物影響", "電解質異常", "電解質紊亂"},
    "ventricular-aneurysm": {"sttchangescompatiblewithventricularaneurysm",
                             "心室瘤", "心室動脈瘤"},
    # Chest-radiograph findings (§6-A NIH ChestX-ray14 image bank). The 14 NIH
    # labels are terse ("Mass", "Effusion"); the builder maps each to the proper
    # clinical phrase below so a Chinese examiner report can evidence the film.
    #
    # NOTE on what is deliberately absent: bare English words whose substring is
    # a *clinically different* phrase are NOT listed as surface forms, because
    # image retrieval looks these up as substrings of the report — "mass" would
    # fire on "massive effusion", "effusion" on "pericardial effusion",
    # "emphysema" on "subcutaneous emphysema", "hernia" on "inguinal hernia".
    # The zh-TW forms are the primary matching path (the examiner writes 中文),
    # so dropping the risky bare English costs recall we do not actually need.
    "atelectasis": {"atelectasis", "肺塌陷", "肺不張", "肺膨脹不全"},
    "cardiomegaly": {"cardiomegaly", "心臟肥大", "心臟擴大", "心影擴大", "心臟增大"},
    "consolidation": {"consolidation", "肺實變", "實變", "肺部實質化", "實質化"},
    "pulmonary-edema": {"pulmonaryedema", "肺水腫", "肺部水腫"},
    "pulmonary-emphysema": {"pulmonaryemphysema", "肺氣腫", "肺部氣腫"},
    "pulmonary-fibrosis": {"pulmonaryfibrosis", "肺纖維化", "肺部纖維化"},
    "diaphragmatic-hernia": {"diaphragmatichernia", "hiatalhernia", "hiatushernia",
                             "橫膈疝", "橫膈膜疝", "橫膈裂孔疝", "食道裂孔疝"},
    "pulmonary-infiltrate": {"pulmonaryinfiltrate", "infiltration", "infiltrate",
                             "infiltrates", "浸潤", "肺浸潤"},
    "pulmonary-mass": {"pulmonarymass", "lungmass", "腫塊", "肺腫塊", "肺部腫塊"},
    "pulmonary-nodule": {"pulmonarynodule", "lungnodule", "nodule", "nodules",
                         "結節", "肺結節"},
    "pleural-thickening": {"pleuralthickening", "肋膜增厚", "胸膜增厚"},
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
