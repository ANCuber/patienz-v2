import os
import google.generativeai as genai

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

LAB_ADVISOR_INSTRUCTION = """你是一位資深臨床顧問，負責對醫學生剛完成的單次輔助檢查及其判讀提供即時、簡短的回饋。

收到的資訊：
1. 病人設定（疾病、基本資料）
2. 本次檢查的項目、結果
3. 學生對結果的判讀
4. 學生標註此檢查欲鑑別/排除的初步鑑別診斷（可能為空）

回饋原則：
- **不可洩漏正確診斷**，不指名疾病
- 若學生判讀正確且完整：給予 1-2 句肯定，並指出此結果對哪些鑑別有意義
- 若學生判讀有誤或遺漏：以提問方式引導（例如「請再注意 XXX 數值」），不直接給答案
- 若學生勾選之 target_ddx 與檢查不匹配：簡短指出「此項檢查對於 X 的鑑別貢獻有限，建議搭配 Y」
- 全部使用繁體中文，使用全形標點符號
- **總長度限制 80 字以內**，三句話以內
"""


def create_lab_advisor_model(problem: str):
    # max_output_tokens 必須足夠涵蓋 Gemini 2.5 Flash 的 thinking tokens + 實際輸出，
    # 否則回饋會在輸出中途被截斷。80 字回饋本身僅需約 200 tokens，但 thinking 可能再用上千 tokens。
    generation_config = {
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 2048,
        "response_mime_type": "text/plain",
    }

    return genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        generation_config=generation_config,
        system_instruction=f"{LAB_ADVISOR_INSTRUCTION}\n\n病人設定：\n{problem}",
    )


def request_lab_feedback(problem: str, exam_entry: dict) -> str:
    model = create_lab_advisor_model(problem)
    chat = model.start_chat()

    target_ddx = exam_entry.get("target_ddx") or []
    msg = (
        f"## 本次檢查\n"
        f"類別：{exam_entry.get('category', '')}\n"
        f"項目：{exam_entry.get('subcategory', '')}\n"
        f"細項：{'、'.join(exam_entry.get('items_chinese', []))}\n\n"
        f"## 結果\n{exam_entry.get('result_html', '')}\n\n"
        f"## 學生判讀\n{exam_entry.get('interpretation', '')}\n\n"
        f"## 學生標註之欲鑑別項目\n{'、'.join(target_ddx) if target_ddx else '（未標註）'}\n\n"
        f"請依系統指令給予簡短回饋。"
    )
    try:
        return chat.send_message(msg).text
    except Exception as e:
        return f"（AI 回饋暫時無法取得：{e}）"
