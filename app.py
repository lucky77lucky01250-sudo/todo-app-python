import datetime
import uuid

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# ===== 設定 =====
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["ID", "タイトル", "内容", "期日", "状態"]

st.set_page_config(page_title="Todoリスト", page_icon="✅", layout="centered")


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

    # 1行目にヘッダーが無ければ作成する
    if ws.row_values(1) != HEADER:
        ws.update(values=[HEADER], range_name="A1:E1")
    return ws


def load_todos(ws):
    """全Todoをデータフレームで取得する。"""
    records = ws.get_all_records(expected_headers=HEADER)
    df = pd.DataFrame(records, columns=HEADER)
    return df


def add_todo(ws, title, content, due):
    """新しいTodoを1行追加する。"""
    ws.append_row([str(uuid.uuid4())[:8], title, content, str(due), "未完了"])


def update_todo(ws, todo_id, title, content, due, done):
    """指定IDのTodoを更新する。"""
    cell = ws.find(todo_id, in_column=1)
    row = cell.row
    ws.update(values=[[title, content, str(due), done]], range_name=f"B{row}:E{row}")


def delete_todo(ws, todo_id):
    """指定IDのTodoを削除する。"""
    cell = ws.find(todo_id, in_column=1)
    ws.delete_rows(cell.row)


# ===== 画面 =====
st.title("✅ Todoリスト")

try:
    ws = get_worksheet()
except Exception as e:
    st.error("スプレッドシートに接続できませんでした。secrets設定を確認してください。")
    st.exception(e)
    st.stop()

tab_list, tab_add, tab_edit = st.tabs(["📋 一覧", "➕ 新規登録", "✏️ 編集・削除"])

# --- 一覧ページ ---
with tab_list:
    df = load_todos(ws)
    if df.empty:
        st.info("まだやることが登録されていません。「新規登録」から追加してください。")
    else:
        st.caption(f"全 {len(df)} 件")
        for _, row in df.iterrows():
            done = str(row["状態"]) == "完了"
            mark = "✅" if done else "⬜️"
            title = f"~~{row['タイトル']}~~" if done else f"**{row['タイトル']}**"
            with st.container(border=True):
                st.markdown(f"{mark} {title}")
                if row["内容"]:
                    st.write(row["内容"])
                if row["期日"]:
                    st.caption(f"📅 期日: {row['期日']}")

# --- 新規登録ページ ---
with tab_add:
    with st.form("add_form", clear_on_submit=True):
        title = st.text_input("タイトル *")
        content = st.text_area("内容")
        due = st.date_input("期日", value=datetime.date.today())
        submitted = st.form_submit_button("登録する")
        if submitted:
            if not title.strip():
                st.warning("タイトルは必須です。")
            else:
                add_todo(ws, title.strip(), content.strip(), due)
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

        with st.form("edit_form"):
            e_title = st.text_input("タイトル *", value=target["タイトル"])
            e_content = st.text_area("内容", value=target["内容"])
            e_due = st.date_input("期日", value=due_value)
            e_done = st.checkbox("完了にする", value=str(target["状態"]) == "完了")

            col1, col2 = st.columns(2)
            with col1:
                update_btn = st.form_submit_button("更新する", use_container_width=True)
            with col2:
                delete_btn = st.form_submit_button(
                    "削除する", type="secondary", use_container_width=True
                )

            if update_btn:
                if not e_title.strip():
                    st.warning("タイトルは必須です。")
                else:
                    done_value = "完了" if e_done else "未完了"
                    update_todo(ws, todo_id, e_title.strip(), e_content.strip(), e_due, done_value)
                    st.success("更新しました！")
                    st.rerun()

            if delete_btn:
                delete_todo(ws, todo_id)
                st.success("削除しました！")
                st.rerun()
