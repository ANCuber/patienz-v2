import os
import google.generativeai as genai
from util.tools import getPDF
import streamlit as st

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
PATIENT_INSTRUCTION = "instruction_file/patient_instruction.txt"

ss = st.session_state

def create_patient_model(problem: str, patient_instruction_path=PATIENT_INSTRUCTION, prior_messages=None):
    pdf_path = f"tmp/{ss.sid}_symptoms.pdf"
    if not os.path.exists(pdf_path):
        with st.spinner("正在搜尋病症特徵..."):
            keyword = ss.data["Problem"]["englishDiseaseName"]
            getPDF(f"{keyword} uptodate clinical features", pdf_path)

    with st.spinner("正在建立病人模型..."):
        with open(patient_instruction_path, 'r', encoding='utf-8') as file:
            patient_instruction = file.read()

        generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 8192,
            "response_mime_type": "text/plain",
        }

        ss.patient_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=generation_config,
            system_instruction=f"{patient_instruction}{problem}",
        )

        if "symptom_pdf_file" not in ss:
            ss.symptom_pdf_file = genai.upload_file(pdf_path, mime_type="application/pdf")

        history = [
            {
                "role": "user",
                "parts": [
                   ss.symptom_pdf_file,
                   "請參照這份文件回答以下的問診。"
                ]
            }
        ]

        if prior_messages:
            for msg in prior_messages:
                role = "user" if msg["role"] == "doctor" else "model"
                history.append({"role": role, "parts": [msg["content"]]})

        ss.patient = ss.patient_model.start_chat(history=history)
    
