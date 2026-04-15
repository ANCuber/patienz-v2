import os
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def create_mark_scheme_setter_model():
    with open("instruction_file/mark_scheme_setter_instruction.txt", 'r', encoding='utf-8') as file:
        instruction = file.read()

    generation_config = {
        "temperature": 0.5,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 65536,
        "response_schema": content.Schema(
            type=content.Type.ARRAY,
            items=content.Schema(
                type=content.Type.OBJECT,
                enum=[],
                required=["category", "item_id", "description", "max_score", "scoring_guide"],
                properties={
                    "category": content.Schema(type=content.Type.STRING),
                    "item_id": content.Schema(type=content.Type.INTEGER),
                    "description": content.Schema(type=content.Type.STRING),
                    "max_score": content.Schema(type=content.Type.INTEGER),
                    "scoring_guide": content.Schema(type=content.Type.STRING),
                },
            )
        ),
        "response_mime_type": "application/json",
    }

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=generation_config,
        system_instruction=instruction,
    )

    return model
