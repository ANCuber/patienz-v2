import streamlit as st
import util.tools as util

ss = st.session_state

util.init(3)
util.note()


def _get_reason(item: dict) -> str:
    return item.get("reason") or item.get("plan") or ""


def _delete_preddx_row(idx: int):
    n = ss._preddx_count
    rows = []
    for i in range(n):
        rows.append({
            "name": ss.get(f"preddx_name_{i}", "").strip(),
            "reason": ss.get(f"preddx_reason_{i}", "").strip(),
            "likelihood": ss.get(f"preddx_lik_{i}", "中"),
        })
    if 0 <= idx < len(rows):
        rows.pop(idx)
    ss.preliminary_ddx = rows
    ss._preddx_count = max(1, n - 1)
    for i in range(n):
        for k in (f"preddx_name_{i}", f"preddx_reason_{i}", f"preddx_lik_{i}"):
            if k in ss:
                del ss[k]


column = st.columns([1, 10, 1, 4])

with column[1]:
    st.header("初步鑑別診斷")
    st.caption(
        "根據目前已蒐集的問診與理學檢查資訊，列出您懷疑的鑑別診斷與支持該鑑別診斷之理由。"
        "送出後此清單將鎖定，不可再修改，作為後續輔助檢查與評分的依據。"
    )

    if not ss.preliminary_ddx_locked:
        if "_preddx_count" not in ss:
            ss._preddx_count = max(3, len(ss.preliminary_ddx))
        if st.button("新增一列", disabled=ss.preliminary_ddx_locked):
            ss._preddx_count += 1

        edits = []
        for i in range(ss._preddx_count):
            existing = ss.preliminary_ddx[i] if i < len(ss.preliminary_ddx) else {}
            with st.container(border=True):
                cols = st.columns([3, 5, 2, 1])
                with cols[0]:
                    name = st.text_input(
                        "鑑別診斷",
                        value=existing.get("name", ""),
                        key=f"preddx_name_{i}",
                        placeholder="如：肺炎",
                    )
                with cols[1]:
                    reason = st.text_area(
                        "支持理由",
                        value=_get_reason(existing),
                        key=f"preddx_reason_{i}",
                        height=70,
                        placeholder="如：發燒、咳痰、胸部叩診實音、聽診有囉音；具糖尿病等危險因子",
                    )
                with cols[2]:
                    likelihood = st.select_slider(
                        "可能性",
                        options=["低", "中", "高"],
                        value=existing.get("likelihood", "中"),
                        key=f"preddx_lik_{i}",
                    )
                with cols[3]:
                    st.markdown(
                        "<div style='height: 1.75rem'></div>",
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "🗑️",
                        key=f"preddx_del_{i}",
                        help="刪除此列",
                        on_click=_delete_preddx_row,
                        args=(i,),
                        disabled=ss._preddx_count <= 1,
                    )
                if name.strip():
                    edits.append({
                        "name": name.strip(),
                        "reason": reason.strip(),
                        "likelihood": likelihood,
                    })

        button_cols = st.columns(2)
        with button_cols[0]:
            if st.button("送出並鎖定清單", use_container_width=True, type="primary"):
                if not edits:
                    st.warning("請至少提出一個鑑別診斷")
                else:
                    ss.preliminary_ddx = edits
                    ss.preliminary_ddx_locked = True
                    util.record(ss.log, f"[Pre-DDx] {edits}")
                    st.rerun()
        with button_cols[1]:
            if st.button("暫存（不鎖定）", use_container_width=True):
                ss.preliminary_ddx = edits
                st.success("已暫存，可繼續編輯")
    else:
        st.success("初步鑑別診斷已鎖定")
        for i, item in enumerate(ss.preliminary_ddx, 1):
            with st.container(border=True):
                st.markdown(f"**{i}. {item['name']}** （可能性：{item['likelihood']}）")
                reason_text = _get_reason(item)
                if reason_text:
                    st.caption(f"支持理由：{reason_text}")

        if util.check_progress() and st.button("進入輔助檢查區", use_container_width=True, type="primary"):
            util.next_page()

with column[3]:
    util.show_patient_profile()

    st.subheader("已完成資料")
    with st.container(border=True):
        st.write(f"問診對話：{len(ss.diagnostic_messages)} 則")
        st.write(f"理學檢查：{len(ss.pe_result)} 項")
