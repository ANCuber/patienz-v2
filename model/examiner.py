import streamlit as st
import util.llm as llm
import util.context_cache as ccache

ss = st.session_state
EXAMINER_INSTRUCTION_TXT = "instruction_file/examiner_instruction_text.txt"
EXAMINER_INSTRUCTION_VAL = "instruction_file/examiner_instruction_val.txt"
PE_INSTRUCTION = "instruction_file/pe_instruction.txt"

# 數值型檢驗的回傳 JSON 結構（與原本完全一致）
_VALUE_RESPONSE_SCHEMA = llm.Schema(
    type=llm.Type.OBJECT,
    properties={
        "value_type_item": llm.Schema(
            type=llm.Type.ARRAY,
            items=llm.Schema(
                type=llm.Type.OBJECT,
                properties={
                    "englishName": llm.Schema(type=llm.Type.STRING),
                    "value": llm.Schema(type=llm.Type.STRING),
                },
            ),
        ),
    },
)


def create_value_examiner_model(problem: str, examiner_instruction_path=EXAMINER_INSTRUCTION_VAL):
    """建立（並快取）數值型檢驗模型；整個 session 只建立一次後重複使用。"""
    if "value_examiner" in ss:
        return

    with st.spinner("正在建立檢查模型..."):
        with open(examiner_instruction_path, "r", encoding="utf-8") as file:
            examiner_instruction = file.read()

        gender = ss.data["基本資訊"]["性別"]

        ss.value_examiner = ccache.start_cached_chat(
            "gemini-2.5-flash",
            system_instruction=f"{examiner_instruction}\n病人性別：{gender}\n\n病人資料：\n{problem}",
            display_name=f"patienz-examval-{ss.get('sid', 'nosid')}",
            temperature=0.4,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            response_schema=_VALUE_RESPONSE_SCHEMA,
            response_mime_type="application/json",
            thinking_budget=llm.THINK_OFF,
        )
        ss.value_examiner_model = True


def create_text_examiner_model(problem: str, examiner_instruction_path=EXAMINER_INSTRUCTION_TXT):
    """建立（並快取）文字型檢查模型；整個 session 只建立一次後重複使用。"""
    if "text_examiner" in ss:
        return

    with st.spinner("正在建立檢查模型..."):
        with open(examiner_instruction_path, "r", encoding="utf-8") as file:
            examiner_instruction = file.read()

        gender = ss.data["基本資訊"]["性別"]

        ss.text_examiner = ccache.start_cached_chat(
            "gemini-2.5-flash",
            system_instruction=f"{examiner_instruction}\n病人性別：{gender}\n\n病人資料：\n{problem}",
            display_name=f"patienz-examtext-{ss.get('sid', 'nosid')}",
            temperature=1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            response_mime_type="text/plain",
            thinking_budget=llm.THINK_OFF,
        )
        ss.text_examiner_model = True


def create_pe_examiner_model(problem: str, pe_instruction_path=PE_INSTRUCTION):
    """建立（並快取）理學檢查模型；整個 session 只建立一次後重複使用。"""
    if "pe_examiner" in ss:
        return

    with st.spinner("正在建立理學檢查模型..."):
        with open(pe_instruction_path, "r", encoding="utf-8") as file:
            pe_instruction = file.read()

        ss.pe_examiner = ccache.start_cached_chat(
            "gemini-2.5-flash",
            system_instruction=f"{pe_instruction}{problem}",
            display_name=f"patienz-pe-{ss.get('sid', 'nosid')}",
            temperature=1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            response_mime_type="text/plain",
            thinking_budget=llm.THINK_OFF,
        )
        ss.pe_examiner_model = True
