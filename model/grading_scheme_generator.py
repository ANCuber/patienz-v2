import os
import json
import re


def _load_grader_template(group):
    path = f"instruction_file/grader_inst_{group}.txt"
    if not os.path.exists(path):
        return []

    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    match = re.search(r"## 評分準則(.*?)## 輸出格式", text, re.S)
    if not match:
        return []

    table = match.group(1).strip()
    lines = [line.strip() for line in table.splitlines() if line.strip()]

    # Remove header lines
    if len(lines) >= 2 and lines[0].startswith("|編號") and "|---" in lines[1]:
        lines = lines[2:]

    items = []
    for line in lines:
        columns = [col.strip() for col in line.split('|') if col.strip()]
        if len(columns) < 3:
            continue

        try:
            item_id = int(columns[0])
        except ValueError:
            continue

        item_text = columns[1]
        full_score = int(re.sub(r"[^0-9]", "", columns[2]) or 0)

        items.append({
            "id": item_id,
            "item": item_text,
            "full_score": full_score,
        })

    # Sort by id to keep deterministic order
    items = sorted(items, key=lambda x: x["id"])
    return items


def _problem_kwargs(problem_data):
    if isinstance(problem_data, str):
        try:
            data = json.loads(problem_data)
        except json.JSONDecodeError:
            data = {"raw": problem_data}
    elif isinstance(problem_data, dict):
        data = problem_data
    else:
        data = {"raw": str(problem_data)}

    if isinstance(data, dict):
        problem_info = data.get("Problem") or data.get("problem") or {}
        disease = problem_info.get("疾病") if isinstance(problem_info, dict) else None
        if not disease and isinstance(data.get("Problem"), str):
            disease = data.get("Problem")
        if not disease and isinstance(data.get("problem"), str):
            disease = data.get("problem")
    else:
        disease = None

    if not disease and isinstance(data, dict) and "Disease" in data:
        disease = data.get("Disease")

    context_text = "".join([str(v) for v in data.values() if isinstance(v, (str, int))])

    return {
        "disease": str(disease) if disease else "",
        "context": context_text,
    }


def _customize_items_template(items, problem_kwargs, group_key):
    customized = [dict(item) for item in items]

    disease = problem_kwargs.get("disease", "").lower()
    context = problem_kwargs.get("context", "").lower()

    # Add 1-2 custom criteria based on disease keyword hints
    extra_items = []
    next_id = max([i["id"] for i in customized], default=0) + 1

    if "胸痛" in disease or "胸痛" in context:
        if group_key == "A":
            extra_items.append({"id": next_id, "item": "確認胸痛性質（放射、緊縮、針刺、一過性）", "full_score": 1})
            next_id += 1
        if group_key == "D":
            extra_items.append({"id": next_id, "item": "將急性冠症候群列為鑑別診斷之一", "full_score": 1})
            next_id += 1

    if "發燒" in disease or "發燒" in context:
        if group_key == "B":
            extra_items.append({"id": next_id, "item": "評估發燒起始與處理方式（用藥、退熱措施）", "full_score": 1})
            next_id += 1
        if group_key == "E":
            extra_items.append({"id": next_id, "item": "提供合適的發燒處置建議（退燒、抗生素評估）", "full_score": 1})
            next_id += 1

    if group_key == "C":
        # always ensure a communication item is present
        found_comm = any("溝通" in item["item"] or "情緒" in item["item"] for item in customized)
        if not found_comm:
            extra_items.append({"id": next_id, "item": "提供同理心與情緒支援的表現", "full_score": 2})
            next_id += 1

    # Add problem-specific item if disease key exists
    if disease and group_key == "E":
        extra_items.append({"id": next_id, "item": f"針對{disease}提出明確處置計畫", "full_score": 2})

    return customized + extra_items


def generate_grading_schemes(problem_data):
    """Generate customized grading schemes for all five groups based on problem_data."""
    problem_kwargs = _problem_kwargs(problem_data)

    scheme = {}
    for group in ["A", "B", "C", "D", "E"]:
        template_items = _load_grader_template(group)
        if not template_items:
            # fallback generic template when file missing
            template_items = [{"id": 1, "item": f"{group}分項初步評估", "full_score": 5}]

        scheme[group] = _customize_items_template(template_items, problem_kwargs, group)

    # Ensure IDs are unique and consistent within each group
    for group, items in scheme.items():
        scheme[group] = sorted(items, key=lambda x: x["id"])

    return json.dumps(scheme, ensure_ascii=False)