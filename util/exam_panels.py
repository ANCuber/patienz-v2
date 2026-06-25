"""Expand a one-click test "panel" (組套) into individual cart entries.

Addresses the repeated feedback that ordering common test sets one item at a
time is tedious ("要一個一個點/可以設定組套", "CBC/DC 不應該一個一個點"). A panel
is just a named list of existing subcategory names; expanding it collects every
test item under those subcategories into the same cart-entry shape the
examination page already uses.

Pure (no Streamlit) so it is unit-testable.
"""


def expand_panels(panel_subcategories, examination_choice, csv_rows, text_type_exams=None):
    """Return de-duplicated cart entries for all items under the given subcategories.

    - ``panel_subcategories``: iterable of subcategory names (e.g. ["血液檢驗", "腎功能檢查"]).
    - ``examination_choice``: the examination_choice.json dict (category -> subcat -> {l, r}).
    - ``csv_rows``: examination.csv parsed as a list of rows ([eng, chinese, ref, unit]).
    - ``text_type_exams``: set of subcategory names whose results are narrative (text).
    """
    text_type_exams = set(text_type_exams or ())
    wanted = set(panel_subcategories or ())
    entries = []
    seen = set()

    for category, subcats in examination_choice.items():
        for subcat, rng in subcats.items():
            if subcat not in wanted:
                continue
            try:
                l = int(rng["l"]) - 1
                r = int(rng["r"]) - 1
            except (KeyError, TypeError, ValueError):
                continue
            for row in csv_rows[l:r]:
                if len(row) < 2 or not row[0].strip():
                    continue
                eng = row[0]
                if eng in seen:
                    continue
                seen.add(eng)
                entries.append({
                    "category": category,
                    "subcategory": subcat,
                    "display": f"{row[1]} {row[0]}",
                    "eng": eng,
                    "chinese": row[1],
                    "reference": row[2] if len(row) > 2 else "",
                    "unit": row[3] if len(row) > 3 else "",
                    "result_type": "text" if subcat in text_type_exams else "value",
                })
    return entries
