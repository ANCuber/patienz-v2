import util.llm as llm

_RESPONSE_SCHEMA = llm.Schema(
    type=llm.Type.ARRAY,
    items=llm.Schema(
        type=llm.Type.OBJECT,
        required=[
            "subcompetency_id",
            "subcompetency_name",
            "domain",
            "level",
            "level_rationale",
            "evidence",
            "improvement",
        ],
        properties={
            "subcompetency_id": llm.Schema(type=llm.Type.STRING),
            "subcompetency_name": llm.Schema(type=llm.Type.STRING),
            "domain": llm.Schema(type=llm.Type.STRING),
            "level": llm.Schema(type=llm.Type.INTEGER),
            "level_rationale": llm.Schema(type=llm.Type.STRING),
            "evidence": llm.Schema(type=llm.Type.STRING),
            "improvement": llm.Schema(type=llm.Type.STRING),
        },
    ),
)


def create_acgme_grader_model(milestone_data: dict, learner_role: dict = None):
    """建立 ACGME Grader 模型實例。

    milestone_data: 已載入的 milestone JSON（由 util.acgme_selector 提供，已過濾不適用子能力）
    learner_role: 學員身份字典，包含 id/label/level_low/level_high/description；
                  缺省時 fallback 到 PGY-1 基準（Level 2-3）。
    """
    with open("instruction_file/acgme_grader_instruction.txt", "r", encoding="utf-8") as f:
        instruction = f.read()

    # 把 milestone 的子能力 + Level 描述塞進 system instruction
    sub_block_lines = [
        "## 本次評核採用之 ACGME 子能力清單",
        f"來源：{milestone_data.get('milestone_source', 'unknown')}",
        f"版本：{milestone_data.get('version', 'unknown')}",
        "註：清單已預先排除 OSCE 單次模擬不適用之子能力（如 Digital Health、Interprofessional Team、Reflective Practice、Wellness、Physician Role in Health Care Systems）；不需也不應對未列於下方的 ACGME 子能力評級。",
        "",
    ]
    for sub in milestone_data.get("subcompetencies", []):
        sub_block_lines.append(
            f"### {sub['id']}（{sub['domain']}）— {sub.get('name_zh', '')} / {sub.get('name_en', '')}"
        )
        for lvl in ("1", "2", "3", "4", "5"):
            desc = sub.get("levels", {}).get(lvl, "")
            sub_block_lines.append(f"- Level {lvl}：{desc}")
        sub_block_lines.append("")

    # === 學員身份段落 ===
    if not learner_role:
        learner_role = {
            "id": "pgy1",
            "label": "PGY-1（畢業後第一年）",
            "level_low": 2,
            "level_high": 3,
            "description": "預設 fallback：未指定學員身份時以 PGY-1 為基準。",
        }
    role_block = [
        "## 本次學員身份",
        f"- 身份：{learner_role.get('label', '')}",
        f"- ID：{learner_role.get('id', '')}",
        f"- 預期 Level 範圍：**{learner_role.get('level_low')}-{learner_role.get('level_high')}**",
        f"- 說明：{learner_role.get('description', '')}",
        "",
        "**評級時請以此範圍為基準**：學員表現符合常規即落在此範圍；超出上限或低於下限均需有具體證據支持。",
    ]

    full_instruction = (
        instruction
        + "\n\n" + "\n".join(role_block)
        + "\n\n" + "\n".join(sub_block_lines)
    )

    config = llm.build_config(
        system_instruction=full_instruction,
        temperature=0.3,
        top_p=0.95,
        top_k=40,
        max_output_tokens=12288,
        response_schema=_RESPONSE_SCHEMA,
        response_mime_type="application/json",
        thinking_budget=llm.THINK_GRADER,
    )
    return llm.ModelHandle("gemini-2.5-flash", config)
