import util.llm as llm

_RESPONSE_SCHEMA = llm.Schema(
    type=llm.Type.ARRAY,
    items=llm.Schema(
        type=llm.Type.OBJECT,
        required=["category", "item_id", "description", "max_score", "score", "feedback"],
        properties={
            "category": llm.Schema(type=llm.Type.STRING),
            "item_id": llm.Schema(type=llm.Type.INTEGER),
            "description": llm.Schema(type=llm.Type.STRING),
            "max_score": llm.Schema(type=llm.Type.INTEGER),
            "score": llm.Schema(type=llm.Type.INTEGER),
            "feedback": llm.Schema(type=llm.Type.STRING),
        },
    ),
)


def create_grader_v2_model(mark_scheme_text: str):
    with open("instruction_file/grader_v2_instruction.txt", "r", encoding="utf-8") as file:
        instruction = file.read()

    full_instruction = f"{instruction}\n\n## 本次評分表\n\n{mark_scheme_text}"

    config = llm.build_config(
        system_instruction=full_instruction,
        temperature=0.3,
        top_p=0.95,
        top_k=40,
        max_output_tokens=16384,
        response_schema=_RESPONSE_SCHEMA,
        response_mime_type="application/json",
        thinking_budget=llm.THINK_GRADER,
    )
    return llm.ModelHandle("gemini-2.5-flash", config)
