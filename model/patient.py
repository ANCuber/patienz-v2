import streamlit as st
import util.llm as llm
import util.context_cache as ccache

PATIENT_INSTRUCTION = "instruction_file/patient_instruction.txt"

ss = st.session_state


def _pediatric_note() -> str:
    """For minors, pin the spoken age to the case age and add a guardian so the
    patient never improvises a different age (fixes the "標註53歲卻回答11歲、無家屬
    陪伴" inconsistency) and pediatric history-taking has a proxy respondent."""
    try:
        age = int(ss.data["基本資訊"]["年齡"])
    except Exception:
        return ""
    if age < 18:
        return (
            f"\n\n## 年齡一致性與兒科設定\n"
            f"- 你的實際年齡是 {age} 歲。任何與年齡/生日相關的提問都必須一致回答 {age} 歲，"
            f"絕不可改口或說出與此不符的年齡。\n"
            f"- 你是未成年病患，由家屬（如家長）陪同就診；較複雜的病史可由家屬代為補充，"
            f"語氣請符合 {age} 歲孩童或其家屬。"
        )
    return ""


def _persona_note() -> str:
    """Inject a configurable personality/emotion (anxious, irritable, taciturn…)
    so students can practice different communication styles. Personality affects
    tone/interaction only — the clinical facts still follow the case JSON."""
    persona = ss.get("patient_persona")
    note = persona.get("note") if isinstance(persona, dict) else None
    if note:
        return (
            f"\n\n## 病人個性／情緒設定\n{note}\n"
            f"（此設定只影響你的語氣與互動風格；你的病情事實仍須與上方病情描述完全一致。）"
        )
    return ""


def create_patient_model(problem: str, patient_instruction_path=PATIENT_INSTRUCTION, prior_messages=None):
    """Build (and cache) the virtual-patient chat.

    Grounding comes from the structured case JSON already embedded in the system
    instruction — the single source of truth for THIS patient. The previous
    runtime Selenium scrape of UpToDate "clinical features" was removed: it was
    the dominant first-turn latency ("爬蟲時間長"), broke on restricted networks
    ("公用網路無法使用"), and tended to make the patient volunteer textbook
    symptoms ("病患太典型、全講出來").
    """
    with st.spinner("正在建立病人模型..."):
        with open(patient_instruction_path, "r", encoding="utf-8") as file:
            patient_instruction = file.read()

        system_instruction = f"{patient_instruction}{problem}{_pediatric_note()}{_persona_note()}"

        history = []
        if prior_messages:
            for msg in prior_messages:
                role = "user" if msg["role"] == "doctor" else "model"
                history.append({"role": role, "parts": [{"text": msg["content"]}]})

        # §1-C: the instruction+case prefix (several k tokens) is identical on
        # every turn → serve it from an explicit context cache when possible.
        # Falls back transparently to the plain path on any cache failure.
        ss.patient = ccache.start_cached_chat(
            "gemini-2.5-flash",
            system_instruction=system_instruction,
            display_name=f"patienz-patient-{ss.get('sid', 'nosid')}",
            history=history,
            temperature=1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            response_mime_type="text/plain",
            safety_settings=llm.safety_block_only_high(),
            thinking_budget=llm.THINK_OFF,  # interactive turn → no hidden thinking latency
        )
        ss.patient_model = True  # sentinel: presence gates re-creation in pages
