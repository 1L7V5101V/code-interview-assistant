"""
配置管理模块
负责读取/写入 config.json，管理 API 配置、快捷键设置等
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o",
    "vision_model": "gpt-4o",
    "language": "python",
    "hotkeys": {
        "toggle_window": "ctrl+b",
        "screenshot": "ctrl+h",
        "screenshot_region": "ctrl+shift+h",
        "prev_question": "ctrl+[",
        "next_question": "ctrl+]",
        "toggle_clickthrough": "ctrl+\\",
        "move_left": "ctrl+left",
        "move_right": "ctrl+right",
        "move_up": "ctrl+up",
        "move_down": "ctrl+down"
    },
    "window_opacity": 0.85,
    "window_pos_x": 100,
    "window_pos_y": 100
}


def load_config() -> dict:
    """加载配置，不存在则创建默认配置"""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 合并默认值（防止新增字段缺失）
        for key, val in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = val
        return config
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> bool:
    """保存配置到 config.json"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_api_config() -> tuple[str, str, str, str]:
    """
    返回 (endpoint, api_key, model, vision_model)
    """
    cfg = load_config()
    return (
        cfg.get("api_endpoint", DEFAULT_CONFIG["api_endpoint"]),
        cfg.get("api_key", ""),
        cfg.get("model", DEFAULT_CONFIG["model"]),
        cfg.get("vision_model", DEFAULT_CONFIG["vision_model"]),
    )


def get_language() -> str:
    return load_config().get("language", "python")


def get_hotkey(action: str) -> str:
    """获取某个动作的快捷键，action 取值：toggle_window/screenshot/prev_question/next_question/toggle_clickthrough"""
    return load_config().get("hotkeys", {}).get(action, DEFAULT_CONFIG["hotkeys"][action])


def update_config(key: str, value):
    """更新单个配置项"""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
