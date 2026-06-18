"""
操作日志模块
记录每次 LLM 调用的请求、响应、错误和耗时，便于复盘。
使用 deque(maxlen=200) 防止内存溢出。
"""

import time
import threading
from collections import deque
from datetime import datetime


class _OperationLogger:
    """单例日志收集器，线程安全"""

    def __init__(self, maxlen=200):
        self._entries: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def log(self, entry: dict):
        """追加一条日志（自动加 timestamp）"""
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._entries.append(entry)

    def get_all(self) -> list[dict]:
        """返回全部日志（按时间倒序）"""
        with self._lock:
            return list(reversed(self._entries))

    def clear(self):
        """清空日志"""
        with self._lock:
            self._entries.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


# 全局单例
op_logger = _OperationLogger()


def format_log_text(entries: list[dict]) -> str:
    """将日志条目列表格式化为可读文本"""
    lines = []
    for e in entries:
        ts = e.get("timestamp", "?")
        op = e.get("op", "?")
        model = e.get("model", "?")
        elapsed = e.get("elapsed", "")
        status = e.get("status", "?")
        line = f"[{ts}] {op}  model={model}  {elapsed}  {status}"
        lines.append(line)

        # 请求摘要
        req = e.get("request_summary", "")
        if req:
            lines.append(f"  请求: {req}")

        # 图像信息
        img_info = e.get("img_info", "")
        if img_info:
            lines.append(f"  图像: {img_info}")

        # 响应摘要
        resp = e.get("response_summary", "")
        if resp:
            lines.append(f"  响应: {resp}")

        # 错误
        err = e.get("error", "")
        if err:
            lines.append(f"  错误: {err}")

        lines.append("")  # 空行分隔

    return "\n".join(lines)
