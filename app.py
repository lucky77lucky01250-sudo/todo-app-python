import datetime
import uuid

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# ===== 設定 =====
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# 列の並び（スプレッドシートのA〜H列に対応）
HEADER = ["ID", "タイトル", "内容", "期日", "重要度", "カテゴリ", "状態", "登録日"]

PRIORITIES = ["高", "中", "低"]
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}
PRIORITY_MARK = {"高": "🔴", "中": "🟡", "低": "🔵"}
CATEGORIES = ["毎日のタスク", "今月の目標", "その他"]

st.set_page_config(page_title="Todoリスト", page_icon="✅", layout="centered")


# ===== パスワードロック（自分専用） =====
def check_password():
    """正しいパスワードを入力した人だけ通す。"""
    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 ログイン")
    st.caption("このアプリは本人専用です。パスワードを入力してください。")
    pw = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        correct = st.secrets.get("auth", {}).get("password")
        if correct and pw == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    return False


# ===== Googleスプレッドシート接続 =====
@st.cache_resource(show_spinner=False)
def get_worksheet():
    """サービスアカウントでスプレッドシートに接続し、ワークシートを返す。"""
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(st.secrets["spreadsheet"]["key"])
    ws = sheet.sheet1

    # 1行目のヘッダーが想定と違えば書き換える
    if ws.row_values(1) != HEADER:
        ws.update(values=[HEADER], range_name="A1:H1")
    return ws


def load_todos(ws):
    """全Todoをデータフレームで取得する。

    ヘッダーの文字に依存せず、2行目以降を列の位置で読む。
    これによりヘッダー名や列数が変わってもクラッシュしない。
    """
    values = ws.get_all_values()
    rows = values[1:] if len(values) > 1 else []
    # 各行を列数に揃える（欠けは空文字で補う）
    normalized = [(row + [""] * len(HEADER))[: len(HEADER)] for row in rows]
    df = pd.DataFrame(normalized, columns=HEADER)
    return df


def add_todo(ws, title, content, due, priority, category):
    """新しいTodoを1行追加する。登録日は自動で本日を記録する。"""
    today = datetime.date.today().isoformat()
    ws.append_row(
        [str(uuid.uuid4())[:8], title, content, str(due), priority, category, "未完了", today]
    )


def update_todo(ws, todo_id, title, content, due, priority, category, status):
    """指定IDのTodoを更新する（登録日とIDは変更しない）。"""
    cell = ws.find(todo_id, in_column=1)
    row = cell.row
    # B〜G列（タイトル・内容・期日・重要度・カテゴリ・状態）を更新
    ws.update(
        values=[[title, content, str(due), priority, category, status]],
        range_name=f"B{row}:G{row}",
    )


def delete_todo(ws, todo_id):
    """指定IDのTodoを削除する。"""
    cell = ws.find(todo_id, in_column=1)
    ws.delete_rows(cell.row)


def sort_todos(df, sort_key):
    """並べ替え。重要度順または期日順。"""
    df = df.copy()
    if sort_key == "重要度順":
        df["_order"] = df["重要度"].map(PRIORITY_ORDER).fillna(99)
        df = df.sort_values(["_order", "期日"]).drop(columns="_order")
    elif sort_key == "期日順":
        df = df.sort_values("期日")
    return df


def render_todo_card(row):
    """1件のTodoをカード表示する。"""
    done = str(row["状態"]) == "完了"
    check = "✅" if done else "⬜️"
    pmark = PRIORITY_MARK.get(str(row["重要度"]), "")
    title = f"~~{row['タイトル']}~~" if done else f"**{row['タイトル']}**"
    with st.container(border=True):
        st.markdown(f"{check} {pmark} {title}")
        if row["内容"]:
            st.write(row["内容"])
        meta = []
        if row["カテゴリ"]:
            meta.append(f"🏷️ {row['カテゴリ']}")
        if row["重要度"]:
            meta.append(f"重要度: {row['重要度']}")
        if row["期日"]:
            meta.append(f"📅 期日: {row['期日']}")
        if meta:
            st.caption("　/　".join(meta))


# ===== 画面 =====
# パスワードロック：認証が通るまで先へ進ませない
if not check_password():
    st.stop()

st.title("✅ Todoリスト")

try:
    ws = get_worksheet()
except Exception as e:
    st.error("スプレッドシートに接続できませんでした。secrets設定を確認してください。")
    st.exception(e)
    st.stop()

tab_list, tab_add, tab_edit, tab_review = st.tabs(
    ["📋 一覧", "➕ 新規登録", "✏️ 編集・削除", "📖 振り返り"]
)

# --- 一覧ページ ---
with tab_list:
    df = load_todos(ws)
    if df.empty:
        st.info("まだやることが登録されていません。「新規登録」から追加してください。")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            cats = ["すべて"] + [c for c in CATEGORIES if c in df["カテゴリ"].values]
            f_cat = st.selectbox("カテゴリ", cats)
        with col2:
            f_status = st.selectbox("状態", ["すべて", "未完了のみ", "完了のみ"])
        with col3:
            f_sort = st.selectbox("並べ替え", ["重要度順", "期日順"])

        view = df.copy()
        if f_cat != "すべて":
            view = view[view["カテゴリ"] == f_cat]
        if f_status == "未完了のみ":
            view = view[view["状態"] != "完了"]
        elif f_status == "完了のみ":
            view = view[view["状態"] == "完了"]
        view = sort_todos(view, f_sort)

        st.caption(f"表示 {len(view)} 件 / 全 {len(df)} 件")
        if view.empty:
            st.info("条件に合うやることがありません。")
        for _, row in view.iterrows():
            render_todo_card(row)

# --- 新規登録ページ ---
with tab_add:
    with st.form("add_form", clear_on_submit=True):
        title = st.text_input("タイトル *")
        content = st.text_area("内容")
        col1, col2 = st.columns(2)
        with col1:
            priority = st.selectbox("重要度", PRIORITIES, index=1)
        with col2:
            category = st.selectbox("カテゴリ", CATEGORIES)
        due = st.date_input("期日", value=datetime.date.today())
        submitted = st.form_submit_button("登録する")
        if submitted:
            if not title.strip():
                st.warning("タイトルは必須です。")
            else:
                add_todo(ws, title.strip(), content.strip(), due, priority, category)
                st.success("登録しました！")
                st.rerun()

# --- 編集・削除ページ ---
with tab_edit:
    df = load_todos(ws)
    if df.empty:
        st.info("編集できるやることがありません。")
    else:
        options = {f"{r['タイトル']} ({r['ID']})": r["ID"] for _, r in df.iterrows()}
        selected_label = st.selectbox("編集するやることを選択", list(options.keys()))
        todo_id = options[selected_label]
        target = df[df["ID"] == todo_id].iloc[0]

        try:
            due_value = datetime.date.fromisoformat(str(target["期日"]))
        except ValueError:
            due_value = datetime.date.today()
        p_index = PRIORITIES.index(target["重要度"]) if target["重要度"] in PRIORITIES else 1
        c_index = CATEGORIES.index(target["カテゴリ"]) if target["カテゴリ"] in CATEGORIES else 0

        with st.form("edit_form"):
            e_title = st.text_input("タイトル *", value=target["タイトル"])
            e_content = st.text_area("内容", value=target["内容"])
            col1, col2 = st.columns(2)
            with col1:
                e_priority = st.selectbox("重要度", PRIORITIES, index=p_index)
            with col2:
                e_category = st.selectbox("カテゴリ", CATEGORIES, index=c_index)
            e_due = st.date_input("期日", value=due_value)
            e_done = st.checkbox("完了にする", value=str(target["状態"]) == "完了")

            col_u, col_d = st.columns(2)
            with col_u:
                update_btn = st.form_submit_button("更新する", use_container_width=True)
            with col_d:
                delete_btn = st.form_submit_button(
                    "削除する", type="secondary", use_container_width=True
                )

            if update_btn:
                if not e_title.strip():
                    st.warning("タイトルは必須です。")
                else:
                    status = "完了" if e_done else "未完了"
                    update_todo(
                        ws, todo_id, e_title.strip(), e_content.strip(),
                        e_due, e_priority, e_category, status,
                    )
                    st.success("更新しました！")
                    st.rerun()

            if delete_btn:
                delete_todo(ws, todo_id)
                st.success("削除しました！")
                st.rerun()

# --- 振り返りページ ---
with tab_review:
    st.subheader("📖 過去の振り返り")
    df = load_todos(ws)
    if df.empty:
        st.info("まだ記録がありません。")
    else:
        # 登録日(YYYY-MM-DD)から「年月(YYYY-MM)」を作る
        df["年月"] = df["登録日"].astype(str).str.slice(0, 7)
        months = sorted([m for m in df["年月"].unique() if len(m) == 7], reverse=True)
        if not months:
            st.info("登録日のある記録がまだありません。新しく登録すると振り返れます。")
        else:
            sel_month = st.selectbox("振り返る月を選ぶ", months)
            month_df = df[df["年月"] == sel_month]

            st.markdown(f"### {sel_month} の記録")
            # 達成度
            total = len(month_df)
            done_cnt = len(month_df[month_df["状態"] == "完了"])
            rate = int(done_cnt / total * 100) if total else 0
            st.metric("達成度", f"{done_cnt} / {total} 件", f"{rate}%")
            st.progress(rate / 100)

            # カテゴリごとに表示
            for cat in CATEGORIES:
                cat_df = month_df[month_df["カテゴリ"] == cat]
                if cat_df.empty:
                    continue
                st.markdown(f"#### 🏷️ {cat}")
                for _, row in sort_todos(cat_df, "重要度順").iterrows():
                    render_todo_card(row)

            # カテゴリ未設定の古いデータ
            other_df = month_df[~month_df["カテゴリ"].isin(CATEGORIES)]
            if not other_df.empty:
                st.markdown("#### 🏷️ （カテゴリなし）")
                for _, row in other_df.iterrows():
                    render_todo_card(row)
