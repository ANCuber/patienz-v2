import os
import google.generativeai as genai
import streamlit as st

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

ss = st.session_state

def create_advisor_model(advisor_instruction_path: str):
    with open(advisor_instruction_path, 'r', encoding='utf-8') as file:
        advisor_instruction = file.read()

    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
        "response_mime_type": "text/plain",
    }

    ss.advisor_model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=generation_config,
        system_instruction=f"{advisor_instruction}",
    )

    pdf_part = ss.symptom_pdf_file if "symptom_pdf_file" in ss else genai.upload_file("tmp/symptom.pdf", mime_type="application/pdf")

    parts = []
    if ss.get("grader_v2_response"):
        parts.append(f"## OSCE 評分結果（v2）\n{ss.grader_v2_response}")
    parts.append(pdf_part)
    parts.append("\n".join([f"{msg['role']}：{msg['content']}" for msg in ss.diagnostic_messages]))

    ss.advisor = ss.advisor_model.start_chat(
        history=[
            {
                "role": "user",
                "parts": parts,
            }
        ],
    )

