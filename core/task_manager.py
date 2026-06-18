"""
任务管理器模块 -- 多题目分层管理
使用 SQLite 存储每道题的完整状态：截图、解答、纠错历史、对话历史
"""

import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "task.db")


def _safe_str(value):
    """将任意类型安全转换为 SQLite 兼容的字符串：list/dict → JSON string"""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str):
        return str(value)
    return value


def get_connection():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            label           TEXT DEFAULT '',
            screenshot_b64  TEXT DEFAULT '',
            title           TEXT DEFAULT '',
            content         TEXT DEFAULT '',
            thinking_process TEXT DEFAULT '',
            initial_code    TEXT DEFAULT '',
            initial_solution TEXT DEFAULT '',
            programming_language TEXT DEFAULT '',
            language        TEXT DEFAULT 'python',
            model           TEXT DEFAULT '',
            mode            TEXT DEFAULT 'coding',
            sub_mode        TEXT DEFAULT 'leetcode',
            history_rounds TEXT DEFAULT '[]',
            created_at      TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS debug_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL,
            code        TEXT DEFAULT '',
            analysis    TEXT DEFAULT '',
            error_analysis TEXT DEFAULT '',
            modifications  TEXT DEFAULT '',
            created_at  TEXT DEFAULT '',
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chat_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT DEFAULT '',
            created_at  TEXT DEFAULT '',
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_debug_task ON debug_entries(task_id, id);
        CREATE INDEX IF NOT EXISTS idx_chat_task  ON chat_entries(task_id, id);
    """)
    # 兼容旧表：为新字段自动添加缺失列
    for col, col_type in [
        ("title", "TEXT DEFAULT ''"),
        ("content", "TEXT DEFAULT ''"),
        ("thinking_process", "TEXT DEFAULT ''"),
        ("programming_language", "TEXT DEFAULT ''"),
        ("sub_mode", "TEXT DEFAULT 'leetcode'"),
        ("history_rounds", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass
    for col, col_type in [
        ("error_analysis", "TEXT DEFAULT ''"),
        ("modifications", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE debug_entries ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  Task CRUD
# ─────────────────────────────────────────────

def create_task(label: str = "", language: str = "python", mode: str = "coding",
                sub_mode: str = "leetcode") -> int:
    """创建新题目，返回 task_id"""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO tasks (label, language, mode, sub_mode, created_at) VALUES (?, ?, ?, ?, ?)",
        (label, language, mode, sub_mode, datetime.now().isoformat())
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_task(task_id: int) -> dict | None:
    """获取单个 task（不含子条目）"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_tasks() -> list[dict]:
    """获取所有 tasks，按创建时间正序"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_task(task_id: int, **kwargs):
    """更新 task 字段"""
    allowed = {"label", "screenshot_b64", "title", "content", "thinking_process",
               "initial_code", "initial_solution", "programming_language",
               "language", "model", "mode", "sub_mode"}
    updates = {k: _safe_str(v) for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_task(task_id: int):
    """删除 task 及其所有关联的 debug/chat 条目（CASCADE）"""
    conn = get_connection()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_all_tasks():
    """清空所有数据"""
    conn = get_connection()
    conn.execute("DELETE FROM debug_entries")
    conn.execute("DELETE FROM chat_entries")
    conn.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()


def get_task_count() -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    conn.close()
    return count


# ─────────────────────────────────────────────
#  Debug History CRUD
# ─────────────────────────────────────────────

def add_debug_entry(task_id: int, code: str, analysis: str,
                   error_analysis: str = "", modifications: str = "") -> int:
    """添加一条纠错记录（自动转换非字符串类型）"""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO debug_entries (task_id, code, analysis, error_analysis, modifications, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (task_id,
         _safe_str(code), _safe_str(analysis),
         _safe_str(error_analysis), _safe_str(modifications),
         datetime.now().isoformat())
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid


def get_debug_entries(task_id: int) -> list[dict]:
    """获取某个 task 的所有纠错记录（正序）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM debug_entries WHERE task_id = ? ORDER BY id ASC",
        (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_debug_entry(entry_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM debug_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ─────────────────────────────────────────────
#  Chat History CRUD
# ─────────────────────────────────────────────

def add_chat_entry(task_id: int, role: str, content: str) -> int:
    """添加一条对话记录"""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO chat_entries (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (task_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid


def get_chat_entries(task_id: int) -> list[dict]:
    """获取某个 task 的所有对话记录（正序）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM chat_entries WHERE task_id = ? ORDER BY id ASC",
        (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
#  完整 task 对象（含子条目）
# ─────────────────────────────────────────────

def get_full_task(task_id: int) -> dict | None:
    """获取完整 task 对象，包含 debug_history 和 chat_history"""
    task = get_task(task_id)
    if task is None:
        return None
    task["debug_history"] = get_debug_entries(task_id)
    task["chat_history"] = get_chat_entries(task_id)
    return task


def get_all_full_tasks() -> list[dict]:
    """获取所有 task 完整对象"""
    tasks = get_all_tasks()
    for t in tasks:
        t["history_rounds"] = get_history_rounds(t["id"])
    return tasks


# ─────────────────────────────────────────────
#  统一迭代轮（Unified Rounds）CRUD
# ─────────────────────────────────────────────

def get_history_rounds(task_id: int) -> list[dict]:
    """读取某 task 的 history_rounds（自动迁移旧数据）"""
    conn = get_connection()
    row = conn.execute(
        "SELECT history_rounds FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return []
    raw = row["history_rounds"] or "[]"
    try:
        rounds = json.loads(raw)
        if isinstance(rounds, list):
            return rounds
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def save_history_rounds(task_id: int, rounds: list[dict]):
    """覆盖保存某 task 的 history_rounds"""
    conn = get_connection()
    conn.execute(
        "UPDATE tasks SET history_rounds = ? WHERE id = ?",
        (json.dumps(rounds, ensure_ascii=False), task_id)
    )
    conn.commit()
    conn.close()


def push_history_round(task_id: int, round_obj: dict) -> int:
    """
    向 history_rounds 追加一条新轮次，返回新的总轮次数。
    round_obj 格式：
    {
        "round_type": "initial" | "debug" | "chat" | "qa",
        "user_input_text": "...",
        "user_screenshots": ["..."],
        "ai_analysis": "...",
        "ai_code": "..."
    }
    """
    rounds = get_history_rounds(task_id)
    rounds.append(round_obj)
    save_history_rounds(task_id, rounds)
    return len(rounds)


def migrate_to_history_rounds(task_id: int) -> bool:
    """
    将旧的 debug_entries + chat_entries 迁移到 history_rounds。
    仅当 history_rounds 为空时执行，幂等。
    返回 True 表示执行了迁移，False 表示已迁移过或无需迁移。
    """
    rounds = get_history_rounds(task_id)
    if rounds:
        return False  # 已经迁移过

    task = get_task(task_id)
    if task is None:
        return False

    new_rounds = []

    # 1. initial 轮：从 task 基本字段构建
    initial_code = (task.get("initial_code") or "").strip()
    initial_thinking = (task.get("thinking_process") or "").strip()
    initial_solution = (task.get("initial_solution") or "").strip()
    ai_analysis = initial_thinking or initial_solution or ""
    if ai_analysis and initial_code:
        ai_analysis = ai_analysis  # 保留，与代码一起展示
    new_rounds.append({
        "round_type": "initial",
        "user_input_text": "",
        "user_screenshots": [task.get("screenshot_b64", "")],
        "ai_analysis": ai_analysis,
        "ai_code": initial_code,
    })

    # 2. 旧 debug_entries → debug 轮
    debugs = get_debug_entries(task_id)
    for d in debugs:
        ea = _safe_str(d.get("error_analysis", ""))
        mods = _safe_str(d.get("modifications", ""))
        analysis_parts = []
        if ea:
            analysis_parts.append(f"【错误分析】\n{ea}")
        if mods:
            analysis_parts.append(f"【修改说明】\n{mods}")
        analysis = "\n\n".join(analysis_parts)
        new_rounds.append({
            "round_type": "debug",
            "user_input_text": "",
            "user_screenshots": [],
            "ai_analysis": analysis,
            "ai_code": d.get("code", ""),
        })

    # 3. 旧 chat_entries → chat 轮（成对合并 user+assistant）
    chats = get_chat_entries(task_id)
    i = 0
    while i < len(chats):
        if chats[i]["role"] == "user":
            user_text = chats[i]["content"]
            ai_text = ""
            if i + 1 < len(chats) and chats[i + 1]["role"] == "assistant":
                ai_text = chats[i + 1]["content"]
                i += 2
            else:
                ai_text = ""
                i += 1
            new_rounds.append({
                "round_type": "chat",
                "user_input_text": user_text,
                "user_screenshots": [],
                "ai_analysis": ai_text,
                "ai_code": "",
            })
        else:
            # 孤立的 assistant 消息
            new_rounds.append({
                "round_type": "chat",
                "user_input_text": "",
                "user_screenshots": [],
                "ai_analysis": chats[i]["content"],
                "ai_code": "",
            })
            i += 1

    if new_rounds:
        save_history_rounds(task_id, new_rounds)
        return True
    return False


# ─────────────────────────────────────────────
#  从旧 history 表迁移数据（兼容）
# ─────────────────────────────────────────────

def migrate_from_old_history():
    """如果存在旧的 history.db 且有数据，迁移到新的 task 表"""
    old_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "history.db")
    if not os.path.exists(old_db):
        return False

    old_conn = sqlite3.connect(old_db)
    old_conn.row_factory = sqlite3.Row
    try:
        rows = old_conn.execute("SELECT * FROM history ORDER BY created_at ASC").fetchall()
    except sqlite3.OperationalError:
        old_conn.close()
        return False
    old_conn.close()

    if not rows:
        return False

    for row in rows:
        rd = dict(row)  # sqlite3.Row 不支持 .get(), 转为 dict
        tid = create_task(
            label=rd.get("question_text") or "",
            language=rd.get("language") or "python",
            mode="coding"
        )
        update_task(tid,
            initial_code=rd.get("code") or "",
            initial_solution=rd.get("solution") or "",
            model=rd.get("model") or ""
        )
    # 迁移后重命名旧数据库文件，避免重复迁移
    bak_path = old_db + ".bak"
    try:
        os.rename(old_db, bak_path)
    except OSError:
        pass
    return True
