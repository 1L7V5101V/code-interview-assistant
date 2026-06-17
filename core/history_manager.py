"""
历史记录管理模块
使用 SQLite 存储每道题的代码答案和解题思路
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "history.db")


def get_connection():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_text TEXT,
            code TEXT,
            solution TEXT,
            language TEXT,
            model TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_entry(question_text: str, code: str, solution: str, language: str, model: str = "") -> int:
    """
    添加一条历史记录，返回新记录的 id
    """
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO history (question_text, code, solution, language, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (question_text, code, solution, language, model, datetime.now().isoformat())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_entry(entry_id: int) -> dict | None:
    """根据 id 获取一条记录"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM history WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all() -> list[dict]:
    """获取所有记录，按创建时间倒序"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM history ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_asc() -> list[dict]:
    """获取所有记录，按创建时间正序（题目编号顺序）"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM history ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_entry(entry_id: int) -> bool:
    """删除一条记录"""
    conn = get_connection()
    conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return True


def get_count() -> int:
    """获取总记录数"""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    conn.close()
    return count


def get_by_index(index: int) -> dict | None:
    """
    按正序索引获取记录（index 从 0 开始）
    用于 上一题/下一题 导航
    """
    rows = get_all_asc()
    if 0 <= index < len(rows):
        return rows[index]
    return None


def get_index_of_id(entry_id: int) -> int:
    """获取某条记录在正序列表中的索引"""
    rows = get_all_asc()
    for i, r in enumerate(rows):
        if r["id"] == entry_id:
            return i
    return -1
