import streamlit as st
import util.llm as llm

ss = st.session_state

PROBLEM_SETTER_INSTRUCTION = "instruction_file/problem_setter_instruction.txt"

# 完整病例 JSON 結構（與原本一致；改用 google-genai 的 Schema 型別）
_RESPONSE_SCHEMA = llm.Schema(
    type=llm.Type.OBJECT,
    required=["基本資訊", "MH", "FH", "SH", "ROS", "VitalSigns", "Problem"],
    properties={
        "基本資訊": llm.Schema(
            type=llm.Type.OBJECT,
            required=["姓名", "年齡", "身高", "體重", "性別", "職業", "生日"],
            properties={
                "姓名": llm.Schema(type=llm.Type.STRING),
                "年齡": llm.Schema(type=llm.Type.INTEGER),
                "身高": llm.Schema(type=llm.Type.INTEGER),
                "體重": llm.Schema(type=llm.Type.NUMBER),
                "性別": llm.Schema(type=llm.Type.STRING),
                "生日": llm.Schema(type=llm.Type.STRING),
                "職業": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "MH": llm.Schema(
            type=llm.Type.OBJECT,
            required=["主訴", "既往疾病", "過敏史", "藥物史", "目前病史"],
            properties={
                "主訴": llm.Schema(type=llm.Type.STRING),
                "既往疾病": llm.Schema(type=llm.Type.STRING),
                "目前病史": llm.Schema(type=llm.Type.STRING),
                "過敏史": llm.Schema(type=llm.Type.STRING),
                "藥物史": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "FH": llm.Schema(
            type=llm.Type.OBJECT,
            required=["直系血親疾病"],
            properties={
                "直系血親疾病": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "SH": llm.Schema(
            type=llm.Type.OBJECT,
            required=["生活習慣", "飲食", "菸酒", "旅遊史"],
            properties={
                "生活習慣": llm.Schema(type=llm.Type.STRING),
                "飲食": llm.Schema(type=llm.Type.STRING),
                "菸酒": llm.Schema(type=llm.Type.STRING),
                "旅遊史": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "ROS": llm.Schema(
            type=llm.Type.OBJECT,
            required=["全身性症狀", "相關系統症狀"],
            properties={
                "全身性症狀": llm.Schema(type=llm.Type.STRING),
                "相關系統症狀": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "VitalSigns": llm.Schema(
            type=llm.Type.OBJECT,
            required=["體溫", "血壓", "心跳", "呼吸次數", "血氧飽和度"],
            properties={
                "體溫": llm.Schema(type=llm.Type.STRING),
                "血壓": llm.Schema(type=llm.Type.STRING),
                "心跳": llm.Schema(type=llm.Type.STRING),
                "呼吸次數": llm.Schema(type=llm.Type.STRING),
                "血氧飽和度": llm.Schema(type=llm.Type.STRING),
            },
        ),
        "Problem": llm.Schema(
            type=llm.Type.OBJECT,
            required=["疾病", "排除可能疾病之診斷", "確認正確疾病之診斷", "處置方式", "englishDiseaseName"],
            properties={
                "疾病": llm.Schema(type=llm.Type.STRING),
                "排除可能疾病之診斷": llm.Schema(type=llm.Type.STRING),
                "確認正確疾病之診斷": llm.Schema(type=llm.Type.STRING),
                "處置方式": llm.Schema(type=llm.Type.STRING),
                "englishDiseaseName": llm.Schema(type=llm.Type.STRING),
            },
        ),
    },
)


def create_problem_setter_model(problem_instruction_path=PROBLEM_SETTER_INSTRUCTION,
                                model_name="gemini-2.5-flash-lite"):
    with open(problem_instruction_path, "r", encoding="utf-8") as file:
        problem_setter_instruction = file.read()

    config = llm.build_config(
        system_instruction=problem_setter_instruction,
        temperature=1,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        response_schema=_RESPONSE_SCHEMA,
        response_mime_type="application/json",
        thinking_budget=llm.THINK_LIGHT,
    )

    ss.problem_setter = llm.start_chat(model_name, config)
    ss.problem_setter_model = True  # sentinel: presence gates re-creation in config page
