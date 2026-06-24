import streamlit as st
import util.llm as llm

ss = st.session_state


def create_advisor_model(advisor_instruction_path: str):
    with open(advisor_instruction_path, "r", encoding="utf-8") as file:
        advisor_instruction = file.read()

    config = llm.build_config(
        system_instruction=advisor_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_mime_type="text/plain",
        thinking_budget=llm.THINK_OFF,
    )

    # Prime with the OSCE result + interview transcript (text only). The former
    # Selenium symptom-PDF grounding was removed; the case JSON and transcript
    # are the authoritative context.
    parts = []
    if ss.get("grader_v2_response"):
        parts.append(f"## OSCE 評分結果（v2）\n{ss.grader_v2_response}")
    parts.append("\n".join([f"{msg['role']}：{msg['content']}" for msg in ss.diagnostic_messages]))
    primer = "\n\n".join(p for p in parts if p)

    history = [{"role": "user", "parts": [{"text": primer}]}] if primer else []

    ss.advisor = llm.start_chat("gemini-2.5-flash", config, history=history)
    ss.advisor_model = True
