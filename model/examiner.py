import os
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content
from util.tools import getPDF
import streamlit as st

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

ss = st.session_state
EXAMINER_INSTRUCTION_TXT = "instruction_file/examiner_instruction_text.txt"
EXAMINER_INSTRUCTION_VAL = "instruction_file/examiner_instruction_val.txt"
PE_INSTRUCTION = "instruction_file/pe_instruction.txt"


def create_value_examiner_model(problem: str, method: str, examiner_instruction_path=EXAMINER_INSTRUCTION_VAL):
    with st.spinner("正在搜尋病症資料..."):
        keyword = st.session_state.data["Problem"]["englishDiseaseName"]
        getPDF(f"{keyword} {method} features", f"tmp/{ss.sid}_{method[:10]}_features.pdf")

    with st.spinner("正在建立檢查模型..."):
        with open(examiner_instruction_path, 'r', encoding='utf-8') as file:
            examiner_instruction = file.read()

        gender = st.session_state.data["基本資訊"]["性別"]

        generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 8192,
            "response_schema": content.Schema(
                type = content.Type.OBJECT,
                properties = {
                    "value_type_item": content.Schema(
                        type = content.Type.ARRAY,
                        items = content.Schema(
                            type = content.Type.OBJECT,
                            properties = {
                                "englishName": content.Schema(type=content.Type.STRING),
                                "value": content.Schema(type=content.Type.STRING),
                            },
                        ),
                    ),
                },
            ),
            "response_mime_type": "application/json",
        }

        ss.examiner_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=generation_config,
            system_instruction=f"{examiner_instruction}\n病人性別：{gender}\n\n病人資料：\n{problem}",
        )

        ss.examiner = ss.examiner_model.start_chat(
            history=[
                {
                    "role": "user",
                    "parts": [
                        genai.upload_file(f"tmp/{ss.sid}_{method[:10]}_features.pdf", mime_type="application/pdf"),
                       "請參考此文獻資料，為以下檢查項目生成檢驗結果："
                    ]
                }
            ],
        )
    

def create_text_examiner_model(problem: str, method: str, examiner_instruction_path=EXAMINER_INSTRUCTION_TXT):
    with st.spinner("正在搜尋病症資料..."):
        keyword = st.session_state.data["Problem"]["englishDiseaseName"]
        getPDF(f"{keyword} \"{method}\" features", f"tmp/{ss.sid}_{method[:10]}_features.pdf")

    with st.spinner("正在建立檢查模型..."):
        with open(examiner_instruction_path, 'r', encoding='utf-8') as file:
            examiner_instruction = file.read()

        gender = st.session_state.data["基本資訊"]["性別"]

        generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 8192,
            "response_mime_type": "text/plain",
        }

        ss.examiner_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=generation_config,
            system_instruction=f"{examiner_instruction}\n病人性別：{gender}\n\n病人資料：\n{problem}",
        )

        ss.examiner = ss.examiner_model.start_chat(
            history=[
                {
                    "role": "user",
                    "parts": [
                        genai.upload_file(f"tmp/{ss.sid}_{method[:10]}_features.pdf", mime_type="application/pdf"),
                       "請參考此文獻資料，為以下檢查項目生成檢查報告："
                    ]
                }
            ],
        )


def create_pe_examiner_model(problem: str, body_systems: str, pe_instruction_path=PE_INSTRUCTION):
    with st.spinner("正在搜尋病症資料..."):
        keyword = st.session_state.data["Problem"]["englishDiseaseName"]
        getPDF(f"{keyword} physical examination findings", f"tmp/{ss.sid}_pe_features.pdf")

    with st.spinner("正在建立理學檢查模型..."):
        with open(pe_instruction_path, 'r', encoding='utf-8') as file:
            pe_instruction = file.read()

        generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 8192,
            "response_mime_type": "text/plain",
        }

        ss.pe_examiner_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=generation_config,
            system_instruction=f"{pe_instruction}{problem}",
        )

        ss.pe_examiner = ss.pe_examiner_model.start_chat(
            history=[
                {
                    "role": "user",
                    "parts": [
                        genai.upload_file(f"tmp/{ss.sid}_pe_features.pdf", mime_type="application/pdf"),
                       "請參考此文獻資料，為以下身體系統生成理學檢查發現："
                    ]
                }
            ],
        )
