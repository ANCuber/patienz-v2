import json
import os
import datetime
import streamlit as st

ss = st.session_state

SAVE_DIR = "data/save"
GRADING_DIR = "data/grading_results"

# Keys whose values are JSON-serializable session state worth persisting.
# AI model objects, audio files, and chat handles are skipped — they will be
# recreated lazily after load.
SAVE_KEYS = [
    "sid", "log",
    "page_id", "current_progress",
    "first_entry",
    "diagnostic_messages",
    "pe_result",
    "examination_result",
    "examination_history",
    "ordered_exam_set",
    "advice_messages",
    "advisor_qa_messages",
    "start_time",
    "data", "problem", "user_config",
    "diagnosis", "ddx", "treatment",
    "preliminary_ddx", "preliminary_ddx_locked",
    "comorbidities",
    "final_ddx_status",
    "note",
    "diagnostic_ended",
    "v2_score_percentage",
    "grader_v2_response", "mark_scheme_raw",
    "show_all", "cur_show_all",
    "config_type",
    "acgme_learner_role",
]


def _ensure_dir():
    os.makedirs(SAVE_DIR, exist_ok=True)


def _serialize(value):
    if isinstance(value, set):
        return {"__set__": [_serialize(v) for v in value]}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _deserialize(value):
    if isinstance(value, dict):
        if list(value.keys()) == ["__set__"]:
            return set(_deserialize(value["__set__"]))
        return {k: _deserialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deserialize(v) for v in value]
    return value


def save_progress() -> str:
    """將目前進度存成 JSON 檔，回傳檔名。"""
    _ensure_dir()

    save_data = {}
    for key in SAVE_KEYS:
        if key in ss:
            try:
                save_data[key] = _serialize(ss[key])
            except (TypeError, ValueError):
                pass

    progress_idx = ss.get("current_progress", 0)
    import util.constants as const
    progress_label = const.noun[progress_idx] if 0 <= progress_idx < len(const.noun) else "未知"

    name = "未命名"
    if isinstance(ss.get("data"), dict):
        try:
            name = ss.data["基本資訊"]["姓名"]
        except (KeyError, TypeError):
            pass

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    file_name = f"{timestamp}_{name}_{progress_label}.json"

    with open(os.path.join(SAVE_DIR, file_name), "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    return file_name


def list_saves():
    _ensure_dir()
    return sorted(
        [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")],
        reverse=True,
    )


def load_progress(file_name: str):
    """從存檔還原 session state。AI 模型於使用時延遲重建。"""
    import util.constants as const
    with open(os.path.join(SAVE_DIR, file_name), "r", encoding="utf-8") as f:
        save_data = json.load(f)

    for key, value in save_data.items():
        ss[key] = _deserialize(value)

    # Backward-compat: pad stage-indexed arrays for sessions saved before pre_ddx existed
    n = len(const.section_name)
    if isinstance(ss.get("first_entry"), list) and len(ss.first_entry) < n:
        ss.first_entry = ss.first_entry + [True] * (n - len(ss.first_entry))
    if isinstance(ss.get("start_time"), list) and len(ss.start_time) < n:
        ss.start_time = ss.start_time + [None] * (n - len(ss.start_time))

    # Backward-compat: ensure new ss keys exist
    for key, default in (
        ("preliminary_ddx", []),
        ("preliminary_ddx_locked", False),
        ("comorbidities", ""),
        ("final_ddx_status", {}),
    ):
        if key not in ss:
            ss[key] = default

    # Drop transient model handles so pages recreate them with restored history.
    for key in ("patient", "patient_model", "examiner", "pe_examiner",
                "advisor", "audio", "audio2", "prompt"):
        if key in ss:
            del ss[key]


def delete_save(file_name: str):
    path = os.path.join(SAVE_DIR, file_name)
    if os.path.exists(path):
        os.remove(path)


def _safe_json_loads(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def save_grading_result() -> str:
    """評分完成後自動存檔，回傳檔名。以 SID 為主檔名，重新評分時會覆蓋。"""
    os.makedirs(GRADING_DIR, exist_ok=True)

    sid = ss.get("sid") or datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    name = "未命名"
    disease = "未知"
    if isinstance(ss.get("data"), dict):
        try:
            name = ss.data["基本資訊"]["姓名"]
        except (KeyError, TypeError):
            pass
        try:
            disease = ss.data["Problem"]["疾病"]
        except (KeyError, TypeError):
            pass

    record = {
        "sid": sid,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "patient": {"name": name, "disease": disease},
        "diagnosis": {
            "main": ss.get("diagnosis"),
            "ddx": ss.get("ddx"),
            "treatment": ss.get("treatment"),
            "comorbidities": ss.get("comorbidities"),
            "final_ddx_status": ss.get("final_ddx_status"),
        },
        "scores": {
            "v2_percentage": ss.get("v2_score_percentage"),
        },
    }

    # V2 grader 結果（OSCE 主評分）
    if "grader_v2_response" in ss:
        parsed = _safe_json_loads(ss.grader_v2_response)
        if parsed is not None:
            record["grader_v2"] = parsed
        else:
            record["grader_v2_raw"] = ss.grader_v2_response

    # 動態評分表 mark scheme
    if "mark_scheme_raw" in ss:
        parsed = _safe_json_loads(ss.mark_scheme_raw)
        if parsed is not None:
            record["mark_scheme"] = parsed
        else:
            record["mark_scheme_raw"] = ss.mark_scheme_raw

    # ACGME 核心能力評核
    if "acgme_grader_parsed" in ss:
        record["acgme"] = {
            "milestone_used": ss.get("acgme_milestone_used"),
            "selection_meta": ss.get("acgme_selection_meta"),
            "learner_role": ss.get("acgme_learner_role"),
            "subcompetencies": ss.acgme_grader_parsed,
            "domain_summary": ss.get("acgme_domain_summary"),
        }
    elif ss.get("acgme_error"):
        record["acgme_error"] = True

    file_name = f"{sid}_{name}_{disease}.json"
    path = os.path.join(GRADING_DIR, file_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)

    return file_name
