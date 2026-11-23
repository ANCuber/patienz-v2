"""Bind a case's fixed imaging reports to real films (§6-A, image-first).

A case may carry ``Problem.檢查預期所見`` — the exact ECG / chest-film report
that ``page/examination.py`` serves verbatim when the student orders that exam.
Writing the report once, in advance, is what makes it deterministic. This
module goes one step further: it picks the real film the report will be paired
with *before* the case is ever played, hands the film's full annotation (every
cardiologist statement, axis, infarct stadium, devices, side/zone/size) to a
rewriting step, and keeps the rewritten report only if it still retrieves that
same film and mentions everything the film is known to show. The report is
therefore written *from* the film, not matched to it afterwards — the only way
the two can never disagree.

Streamlit-free and network-optional: with no rewriter (no API key) the binding
is recorded against the original text when a film already matches it.
"""
import json
import re

from util import image_bank as ib

EXAM_ITEM_TERMS = {"心電圖": ["Electrocardiography", "心電圖"],
                   "胸部X光": ["Chest X-ray", "胸部X光"]}
_TAG_RE = re.compile(r"\[\s*(NORMAL|ABNORMAL)\s*\]", re.I)


def _demo(data):
    basic = data.get("基本資訊") if isinstance(data, dict) else None
    basic = basic if isinstance(basic, dict) else {}
    try:
        age = int(basic.get("年齡")) if basic.get("年齡") not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    return age, basic.get("性別")


def _modality_for(key):
    return ib.resolve_modality(key, " ".join(EXAM_ITEM_TERMS.get(key, [])))


def _keep_tag(original, rewritten):
    """The examiner tag is machine-read; make sure the rewrite still ends in one
    and that it is the original's (a rewrite must not flip normal/abnormal)."""
    m = _TAG_RE.search(original or "")
    tag = "[%s]" % m.group(1).upper() if m else None
    body = _TAG_RE.sub("", rewritten or "").rstrip()
    if not body:
        return None
    return body + ("\n" + tag if tag else "")


def unmentioned_findings(film, text):
    """Validated findings on the film that ``text`` does not evidence."""
    missing = []
    for term in ib.claim_terms(film):
        if not ib._evidence_count([term], text):
            missing.append(term)
    return missing


def rewrite_prompt(data, key, expected, film):
    basic = data.get("基本資訊", {}) if isinstance(data, dict) else {}
    problem = data.get("Problem", {}) if isinstance(data, dict) else {}
    brief = ib.annotation_brief_zh(film)
    zh_hint = ""
    ann = film.get("annotation") if isinstance(film.get("annotation"), dict) else {}
    missing = unmentioned_findings(film, expected)
    if missing:
        zh_of = {s.get("description"): s.get("zh") for s in (ann.get("statements") or [])
                 if isinstance(s, dict)}
        zh_hint = "\n目前預期報告**尚未提到**影像上已標註的：%s。改寫時必須納入。" % "、".join(
            zh_of.get(m) or m for m in missing)
    return (
        "你是 OSCE 虛擬病人平台的檢查報告撰寫者。\n\n"
        "【病例摘要】疾病：%s；病人：%s，%s 歲；主訴：%s\n\n"
        "【原本預期的〈%s〉報告】\n%s\n\n"
        "【系統已為此病例選定的真實影像，其實際所見】\n%s%s\n\n"
        "請改寫〈%s〉報告，使其**完整且精確地描述這張影像的實際所見**：\n"
        "1. 影像上每一項已標註的所見都必須寫進報告（含定位／導程、電軸、梗塞分期、側別、肺野、大小、裝置與管線）；\n"
        "2. 不可描述影像上沒有的所見；若原本預期報告有影像上不存在的所見，刪除或改為陰性發現；\n"
        "3. 保留與影像不衝突的病例臨床意圖與正常項目；\n"
        "4. 維持 markdown 格式：「**[檢查名稱] 檢查結果：**」「- **整體印象：**」「- **具體發現：**」「- **重要陰性發現：**」；\n"
        "5. 最後一行只寫原本的 [ABNORMAL] 或 [NORMAL] 標記；\n"
        "6. 只輸出報告，不加說明。"
        % (problem.get("疾病", ""), basic.get("性別", ""), basic.get("年齡", ""),
           (data.get("MH", {}) or {}).get("主訴", "") if isinstance(data, dict) else "",
           key, expected, brief or "（此影像沒有額外標註）", zh_hint, key)
    )


def gemini_rewriter(model_name="gemini-2.5-flash"):
    """Rewriter backed by Gemini; imported lazily so tests need no client."""
    from util import llm

    def _rewrite(prompt):
        cfg = llm.build_config(temperature=0.2, max_output_tokens=2048,
                               thinking_budget=llm.THINK_LIGHT)
        resp = llm.generate(model_name, prompt, cfg)
        return getattr(resp, "text", None) or ""
    return _rewrite


def bind_case_to_films(data, *, rewrite=None, retries=1, manifest=None, directory=None,
                       log=None) -> dict:
    """Bind each fixed report in ``data`` to a real film, rewriting it to the
    film's annotation when a rewriter is available. Mutates ``data`` in place:
    updates ``Problem.檢查預期所見[key]`` and records ``data["_image_binding"]``.

    Returns the binding summary: ``{key: {"target_id", "rewritten", "reason"}}``.
    """
    if not isinstance(data, dict):
        return {}
    problem = data.get("Problem")
    fixed = problem.get("檢查預期所見") if isinstance(problem, dict) else None
    if not isinstance(fixed, dict) or not fixed:
        return {}
    age, sex = _demo(data)
    man = manifest if manifest is not None else ib.load_manifest(directory)
    binding = {}
    for key, expected in list(fixed.items()):
        if not isinstance(expected, str) or not expected.strip():
            continue
        modality = _modality_for(key)
        if not modality:
            binding[key] = {"target_id": None, "rewritten": False, "reason": "unknown modality"}
            continue
        terms = EXAM_ITEM_TERMS.get(key, [key])
        film = ib.preselect_for_case(modality, expected, patient_age=age, patient_sex=sex,
                                     item_terms=terms, directory=directory, manifest=man)
        if film is None:
            binding[key] = {"target_id": None, "rewritten": False,
                            "reason": "no film clears the gate for this report"}
            continue
        current, rewritten, reason = expected, False, "bound to original text"
        missing = unmentioned_findings(film, current)
        if rewrite is not None and (missing or ib.annotation_brief_zh(film)):
            attempt_text = current
            for attempt in range(retries + 1):
                try:
                    draft = rewrite(rewrite_prompt(data, key, attempt_text, film))
                except Exception as exc:  # noqa: BLE001 - the case must still load
                    reason = "rewriter failed: %s" % exc
                    if log:
                        log("[IMAGE-BIND] %s: %s" % (key, reason))
                    break
                draft = _keep_tag(current, draft)
                if not draft:
                    reason = "rewriter returned nothing"
                    break
                again = ib.preselect_for_case(modality, draft, patient_age=age, patient_sex=sex,
                                              item_terms=terms, directory=directory, manifest=man)
                still_missing = unmentioned_findings(film, draft)
                if again is not None and again.get("id") == film.get("id") and not still_missing:
                    current, rewritten, reason = draft, True, "rewritten to the film"
                    break
                reason = ("rewrite retrieved %s instead" % (again.get("id") if again else "nothing")
                          if (again is None or again.get("id") != film.get("id"))
                          else "rewrite still omits: %s" % ", ".join(still_missing))
                attempt_text = draft
            if not rewritten and log:
                log("[IMAGE-BIND] %s: kept original (%s)" % (key, reason))
        elif missing:
            reason = "film has unmentioned findings and no rewriter: %s" % ", ".join(missing)
        fixed[key] = current
        binding[key] = {"target_id": film.get("id"), "rewritten": rewritten, "reason": reason,
                        "unmentioned": unmentioned_findings(film, current)}
        if log:
            log("[IMAGE-BIND] %s -> %s (%s)" % (key, film.get("id"), reason))
    data["_image_binding"] = binding
    return binding


def prompt_json(data) -> str:
    """The case as the patient/examiner prompts should see it: underscore-
    prefixed bookkeeping (``_template_meta``, ``_image_binding``) stripped."""
    clean = {k: v for k, v in (data or {}).items() if not str(k).startswith("_")}
    return json.dumps(clean, ensure_ascii=False, indent=2)
