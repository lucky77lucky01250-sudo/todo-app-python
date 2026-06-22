import datetime
import hmac
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
        # タイミング攻撃に強い比較
        if correct and hmac.compare_digest(str(pw), str(correct)):
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

    # 1行目が「空のときだけ」ヘッダーを作る。
    # 既存データがある場合は絶対に上書きしない（データ破壊防止）。
    if not ws.row_values(1):
        ws.update(values=[HEADER], range_name="A1:H1")
    return ws


@st.cache_data(ttl=60, show_spinner=False)
def load_todos():
    """全Todoをデータフレームで取得する（60秒キャッシュ）。

    ヘッダーの文字に依存せず、2行目以降を列の位置で読む。
    更新系の操作後は load_todos.clear() でキャッシュを破棄する。
    """
    ws = get_worksheet()
    values = ws.get_all_values()
    rows = values[1:] if len(values) > 1 else []
    normalized = [(row + [""] * len(HEADER))[: len(HEADER)] for row in rows]
    return pd.DataFrame(normalized, columns=HEADER)


def find_row_by_id(ws, todo_id):
    """ID列(A列)を完全一致で探し、行番号(1始まり)を返す。無ければNone。"""
    ids = ws.col_values(1)
    for i, value in enumerate(ids):
        if value == todo_id:
            return i + 1
    return None


def add_todo(ws, title, content, due, priority, category):
    """新しいTodoを1行追加する。登録日は自動で本日を記録する。"""
    today = datetime.date.today().isoformat()
    new_id = str(uuid.uuid4())  # 衝突しないフルUUID
    ws.append_row(
        [new_id, title, content, str(due), priority, category, "未完了", today]
    )


def update_todo(ws, todo_id, title, content, due, priority, category, status):
    """指定IDのTodoを更新する（登録日とIDは変更しない）。見つからなければFalse。"""
    row = find_row_by_id(ws, todo_id)
    if row is None:
        return False
    ws.update(
        values=[[title, content, str(due), priority, category, status]],
        range_name=f"B{row}:G{row}",
    )
    return True


def set_status(ws, todo_id, status):
    """状態(G列)だけを更新する。見つからなければFalse。"""
    row = find_row_by_id(ws, todo_id)
    if row is None:
        return False
    ws.update(values=[[status]], range_name=f"G{row}")
    return True


def delete_todo(ws, todo_id):
    """指定IDのTodoを削除する。見つからなければFalse。"""
    row = find_row_by_id(ws, todo_id)
    if row is None:
        return False
    ws.delete_rows(row)
    return True


def sort_todos(df, sort_key):
    """並べ替え。重要度順または期日順（期日なしは末尾）。"""
    df = df.copy()
    df["_due"] = pd.to_datetime(df["期日"], errors="coerce")
    if sort_key == "重要度順":
        df["_pri"] = df["重要度"].map(PRIORITY_ORDER).fillna(99)
        df = df.sort_values(["_pri", "_due"], na_position="last")
        df = df.drop(columns=["_pri", "_due"])
    else:  # 期日順
        df = df.sort_values("_due", na_position="last").drop(columns="_due")
    return df


def due_label(due_str):
    """期日を「今日 / ●日遅れ / 明日」など強調表示にする。"""
    due_str = str(due_str).strip()
    if not due_str:
        return ""
    try:
        d = datetime.date.fromisoformat(due_str)
    except ValueError:
        return f"📅 期日: {due_str}"
    delta = (d - datetime.date.today()).days
    if delta < 0:
        return f"⚠️ {abs(delta)}日遅れ（{due_str}）"
    if delta == 0:
        return f"🔥 今日（{due_str}）"
    if delta == 1:
        return f"📅 明日（{due_str}）"
    return f"📅 期日: {due_str}"


def render_todo_card(row, ws=None, toggle=False):
    """1件のTodoをカード表示する。toggle=Trueなら完了切替を付ける。"""
    done = str(row["状態"]) == "完了"
    pmark = PRIORITY_MARK.get(str(row["重要度"]), "")
    title = f"~~{row['タイトル']}~~" if done else f"**{row['タイトル']}**"
    with st.container(border=True):
        if toggle and ws is not None:
            c1, c2 = st.columns([0.1, 0.9])
            with c1:
                new = st.checkbox(
                    "完了", value=done, key=f"chk_{row['ID']}",
                    label_visibility="collapsed",
                )
            box = c2
            if new != done:
                if set_status(ws, row["ID"], "完了" if new else "未完了"):
                    load_todos.clear()
                st.rerun()
        else:
            box = st.container()

        with box:
            st.markdown(f"{pmark} {title}")
            if row["内容"]:
                st.write(row["内容"])
            meta = []
            if row["カテゴリ"]:
                meta.append(f"🏷️ {row['カテゴリ']}")
            if row["重要度"]:
                meta.append(f"重要度: {row['重要度']}")
            dl = due_label(row["期日"])
            if dl:
                meta.append(dl)
            if meta:
                st.caption("　/　".join(meta))


# ===== 画面 =====
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
    df = load_todos()
    if df.empty:
        st.info("まだやることが登録されていません。「新規登録」から追加してください。")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            cats = ["すべて"] + sorted(
                [c for c in df["カテゴリ"].unique() if str(c).strip()]
            )
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

        st.caption(f"表示 {len(view)} 件 / 全 {len(df)} 件　（チェックで完了切替）")
        if view.empty:
            st.info("条件に合うやることがありません。")
        for _, row in view.iterrows():
            render_todo_card(row, ws=ws, toggle=True)

# --- 新規登録ページ ---
with tab_add:
    due_on = st.checkbox(
        "期日を設定する",
        value=True,
        key="add_due_on",
        help="毎日くり返すタスクなど、期日が不要ならOFFにしてください。",
    )
    with st.form("add_form", clear_on_submit=True):
        title = st.text_input("タイトル *")
        content = st.text_area("内容")
        col1, col2 = st.columns(2)
        with col1:
            priority = st.selectbox("重要度", PRIORITIES, index=1)
        with col2:
            category = st.selectbox("カテゴリ", CATEGORIES)
        due_input = st.date_input("期日", value=datetime.date.today()) if due_on else None
        submitted = st.form_submit_button("登録する")
        if submitted:
            if not title.strip():
                st.warning("タイトルは必須です。")
            else:
                due = due_input if (due_on and due_input) else ""
                try:
                    add_todo(ws, title.strip(), content.strip(), due, priority, category)
                    load_todos.clear()
                    st.success("登録しました！")
                    st.rerun()
                except Exception as e:
                    st.error("登録に失敗しました。少し待って再度お試しください。")
                    st.exception(e)

# --- 編集・削除ページ ---
with tab_edit:
    df = load_todos()
    if df.empty:
        st.info("編集できるやることがありません。")
    else:
        options = {
            f"{r['タイトル']} ({str(r['ID'])[:8]})": r["ID"] for _, r in df.iterrows()
        }
        selected_label = st.selectbox("編集するやることを選択", list(options.keys()))
        todo_id = options[selected_label]
        target = df[df["ID"] == todo_id].iloc[0]

        has_due = bool(str(target["期日"]).strip())
        try:
            due_value = datetime.date.fromisoformat(str(target["期日"]))
        except ValueError:
            due_value = datetime.date.today()
        p_index = PRIORITIES.index(target["重要度"]) if target["重要度"] in PRIORITIES else 1
        c_index = CATEGORIES.index(target["カテゴリ"]) if target["カテゴリ"] in CATEGORIES else 0

        # 期日ON/OFFはフォーム外（切替で即座に日付欄を出し入れするため）
        e_due_on = st.checkbox(
            "期日を設定する",
            value=has_due,
            key=f"edit_due_on_{todo_id}",
            help="毎日くり返すタスクなど、期日が不要ならOFFにしてください。",
        )
        with st.form(f"edit_form_{todo_id}"):
            e_title = st.text_input("タイトル *", value=target["タイトル"])
            e_content = st.text_area("内容", value=target["内容"])
            col1, col2 = st.columns(2)
            with col1:
                e_priority = st.selectbox("重要度", PRIORITIES, index=p_index)
            with col2:
                e_category = st.selectbox("カテゴリ", CATEGORIES, index=c_index)
            e_due_input = st.date_input("期日", value=due_value) if e_due_on else None
            e_done = st.checkbox("完了にする", value=str(target["状態"]) == "完了")
            confirm_del = st.checkbox("削除を確認する（チェック後に削除ボタン）")

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
                    e_due = e_due_input if (e_due_on and e_due_input) else ""
                    try:
                        ok = update_todo(
                            ws, todo_id, e_title.strip(), e_content.strip(),
                            e_due, e_priority, e_category, status,
                        )
                        load_todos.clear()
                        if ok:
                            st.success("更新しました！")
                        else:
                            st.warning("対象が見つかりませんでした（すでに削除された可能性）。")
                        st.rerun()
                    except Exception as e:
                        st.error("更新に失敗しました。少し待って再度お試しください。")
                        st.exception(e)

            if delete_btn:
                if not confirm_del:
                    st.warning("削除するには「削除を確認する」にチェックを入れてください。")
                else:
                    try:
                        ok = delete_todo(ws, todo_id)
                        load_todos.clear()
                        if ok:
                            st.success("削除しました！")
                        else:
                            st.warning("対象が見つかりませんでした（すでに削除済み）。")
                        st.rerun()
                    except Exception as e:
                        st.error("削除に失敗しました。少し待って再度お試しください。")
                        st.exception(e)

# --- 振り返りページ ---
with tab_review:
    st.subheader("📖 過去の振り返り")
    df = load_todos().copy()
    if df.empty:
        st.info("まだ記録がありません。")
    else:
        df["年月"] = df["登録日"].astype(str).str.slice(0, 7)
        months = sorted([m for m in df["年月"].unique() if len(m) == 7], reverse=True)
        if not months:
            st.info("登録日のある記録がまだありません。新しく登録すると振り返れます。")
        else:
            sel_month = st.selectbox("振り返る月を選ぶ", months)
            month_df = df[df["年月"] == sel_month]

            st.markdown(f"### {sel_month} の記録")
            total = len(month_df)
            done_cnt = len(month_df[month_df["状態"] == "完了"])
            rate = int(done_cnt / total * 100) if total else 0
            st.metric("達成度", f"{done_cnt} / {total} 件", f"{rate}%")
            st.progress(rate / 100)

            for cat in CATEGORIES:
                cat_df = month_df[month_df["カテゴリ"] == cat]
                if cat_df.empty:
                    continue
                st.markdown(f"#### 🏷️ {cat}")
                for _, row in sort_todos(cat_df, "重要度順").iterrows():
                    render_todo_card(row)

            other_df = month_df[~month_df["カテゴリ"].isin(CATEGORIES)]
            if not other_df.empty:
                st.markdown("#### 🏷️ （カテゴリなし）")
                for _, row in other_df.iterrows():
                    render_todo_card(row)
