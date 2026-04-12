import os
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def create_grader_v2_model(mark_scheme_text: str):
    with open("instruction_file/grader_v2_instruction.txt", 'r', encoding='utf-8') as file:
        instruction = file.read()

    full_instruction = f"{instruction}\n\n## 本次評分表\n\n{mark_scheme_text}"

    generation_config = {
        "temperature": 0.3,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 65536,
        "response_schema": content.Schema(
            type=content.Type.ARRAY,
            items=content.Schema(
                type=content.Type.OBJECT,
                enum=[],
                required=["category", "item_id", "description", "max_score", "score", "feedback"],
                properties={
                    "category": content.Schema(type=content.Type.STRING),
                    "item_id": content.Schema(type=content.Type.INTEGER),
                    "description": content.Schema(type=content.Type.STRING),
                    "max_score": content.Schema(type=content.Type.INTEGER),
                    "score": content.Schema(type=content.Type.INTEGER),
                    "feedback": content.Schema(type=content.Type.STRING),
                },
            )
        ),
        "response_mime_type": "application/json",
    }

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=generation_config,
        system_instruction=full_instruction,
    )

    return model
