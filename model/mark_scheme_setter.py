import util.llm as llm

_RESPONSE_SCHEMA = llm.Schema(
    type=llm.Type.ARRAY,
    items=llm.Schema(
        type=llm.Type.OBJECT,
        required=["category", "item_id", "description", "max_score", "scoring_guide"],
        properties={
            "category": llm.Schema(type=llm.Type.STRING),
            "item_id": llm.Schema(type=llm.Type.INTEGER),
            "description": llm.Schema(type=llm.Type.STRING),
            "max_score": llm.Schema(type=llm.Type.INTEGER),
            "scoring_guide": llm.Schema(type=llm.Type.STRING),
        },
    ),
)


def create_mark_scheme_setter_model():
    with open("instruction_file/mark_scheme_setter_instruction.txt", "r", encoding="utf-8") as file:
        instruction = file.read()

    config = llm.build_config(
        system_instruction=instruction,
        temperature=0.5,
        top_p=0.95,
        top_k=40,
        max_output_tokens=16384,
        response_schema=_RESPONSE_SCHEMA,
        response_mime_type="application/json",
        thinking_budget=llm.THINK_LIGHT,
    )
    return llm.ModelHandle("gemini-2.5-flash", config)
