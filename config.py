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
        "move_down": "ctrl+down",
        "new_task": "ctrl+n",
        "debug_fix": "ctrl+j",
        "toggle_mode": "ctrl+m"
    },
    "window_opacity": 0.85,
    "window_pos_x": 100,
    "window_pos_y": 100
}

# ── 可配置提示词默认值 ─────────────────

DEFAULT_PROMPTS = {
    "system_solve": (
        "你收到一张编程题截图，需同时完成两项任务：\n"
        "1. 原文转录：将截图中的所有文字逐字逐句原样复制到 content 字段。\n"
        "   不得用自己的话改写，不得概括，不得遗漏任何细节（含示例、约束条件）。\n"
        "   你在此阶段是OCR转录引擎，不是分析引擎。\n"
        "2. 解题：根据题目要求，给出解题思路和完整代码。\n\n"
        "按以下 JSON Schema 返回，不要用 markdown code block 包裹 JSON：\n"
        '{"title": "题目名称", '
        '"content": "截图中题目的原文逐字复制，一字不改", '
        '"thinking_process": "分步骤解题思路", '
        '"code": "完整可运行的带注释解答代码", '
        '"programming_language": "代码语言"}\n\n'
        "只返回 JSON 对象，无其他文字。"
    ),
    "system_qa": (
        "你是一个通用视觉问答助手。用户会提供一张截图并向你提问。\n"
        "请仔细查看图片内容，准确、简洁地回答用户的问题。\n"
        "直接回答即可，不需要做任何假设（如图片一定是代码题）。"
    ),
    "user_qa_solve": (
        "请处理截图内容。\n"
        "【关键】content 字段必须逐字复制截图中的所有原文，一字不改。\n"
        "按以下 JSON Schema 返回，不要用 markdown code block 包裹：\n"
        '{"title": "图片简述", "content": "图片中的原文逐字复制",'
        ' "thinking_process": "对图片内容的分析回答", "code": "", "programming_language": ""}'
    ),
    "system_debug": (
        "你是一个代码审查助手。用户正在做一道编程题，并提供了题目截图和当前代码。\n"
        "请仔细检查代码是否正确，指出问题并给出修正版本。\n"
        "按以下 JSON Schema 返回，不要包含任何解释性文本：\n\n"
        '{"error_analysis": "逐条指出代码中的问题及原因",'
        ' "fixed_code": "修正后的完整代码",'
        ' "modifications": "关键修改点说明"}\n\n'
        "Important: 只返回 JSON 对象，无其他文字。"
    ),
}

# 提示词在设置中的显示名称
PROMPT_LABELS = {
    "system_solve": "System · 代码解题",
    "system_qa": "System · QA 问答",
    "user_qa_solve": "User · QA 截图提问",
    "system_debug": "System · 代码纠错",
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


# ── 提示词管理 ────────────────────────────

def get_prompt(key: str) -> str:
    """获取提示词：优先用户自定义，否则返回默认值"""
    cfg = load_config()
    prompts = cfg.get("prompts", {})
    if key in prompts and prompts[key].strip():
        return prompts[key]
    return DEFAULT_PROMPTS.get(key, "")


def save_prompt(key: str, value: str):
    """保存用户自定义提示词"""
    cfg = load_config()
    if "prompts" not in cfg:
        cfg["prompts"] = {}
    cfg["prompts"][key] = value
    save_config(cfg)


def reset_prompts():
    """重置所有提示词为默认值"""
    cfg = load_config()
    cfg["prompts"] = {}
    save_config(cfg)
