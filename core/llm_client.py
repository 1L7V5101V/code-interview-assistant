"""
LLM 客户端模块
用 raw HTTP 请求调用 OpenAI 兼容 API（支持 vision / 截图识别）
彻底绕过 openai SDK 版本差异问题
"""

import json
import base64
import io
import ssl
import urllib.request
from PIL import Image
import config


# ─────────────────────────────────────────────
#  Raw HTTP 底层封装
# ─────────────────────────────────────────────
def _build_url(endpoint: str) -> str:
    """根据 endpoint 构造 chat/completions URL"""
    e = endpoint.rstrip("/")
    if e.endswith("/v1"):
        return e + "/chat/completions"
    else:
        return e + "/v1/chat/completions"


def _raw_post(url: str, api_key: str, body: dict, timeout: int = 60) -> dict:
    """
    发送 raw HTTP POST，返回解析后的 JSON dict。
    自动处理 HTTPS 证书验证（本地服务可忽略）。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(error_body)
            raise RuntimeError(json.dumps(err_json, ensure_ascii=False)[:500])
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {e.code}: {error_body[:300]}")


def _extract_content(result: dict) -> str:
    """
    从 OpenAI 标准响应 JSON 中提取 content 文本。
    兼容：
      - 正常情况：result["choices"][0]["message"]["content"]
      - content=None（推理模型）：尝试取 reasoning_content
      - 返回字符串（极少数代理）：直接返回
    """
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        raise ValueError(f"未知响应类型：{type(result)}")

    try:
        content = result["choices"][0]["message"]["content"]
        if content is not None:
            return content
        # content=None：尝试取 reasoning_content（DeepSeek-R1 等）
        try:
            return result["choices"][0]["message"].get("reasoning_content", "")
        except Exception:
            return ""
    except (KeyError, IndexError):
        # 响应格式异常，返回原始 JSON 供调试
        return f"[响应格式异常] {json.dumps(result, ensure_ascii=False)[:200]}"


def _image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ─────────────────────────────────────────────
#  提示词模板
# ─────────────────────────────────────────────
SYSTEM_PROMPT_SOLVE = """你是一个编程面试助手，专注于帮助求职者解答手撕代码题。
请分析题目，给出完整可运行的代码和详细解题思路。"""

USER_PROMPT_SOLVE = """请分析截图中的代码题目，按以下格式回答：

## 代码
给出完整可运行的代码（语言：{language}），用 markdown code block 包裹。

## 解题思路
分步骤说明你的思路，包括：
1. 题目理解
2. 算法/数据结构选择
3. 时间/空间复杂度分析
4. 关键细节说明

如果截图中有多道题，请逐一解答。"""

SYSTEM_PROMPT_FIX = """你是一个代码审查助手，帮助找出代码中的错误并给出修正版本。"""

USER_PROMPT_FIX = """这是我的代码（语言：{language}），运行出错/结果不正确。
请按以下格式回答：

## 错误分析
指出代码中的错误及原因。

## 修正代码
给出修正后的完整代码，用 markdown code block 包裹。

## 优化建议
说明可以改进的地方。"""


# ─────────────────────────────────────────────
#  公开 API
# ─────────────────────────────────────────────
def solve_with_screenshot(img: Image.Image, language: str = "python") -> str:
    """
    截图 → 调用视觉模型 → 返回原始文本响应
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)
    b64_img = _image_to_base64(img)

    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_SOLVE},
            {"role": "user", "content": [
                {"type": "text", "text": USER_PROMPT_SOLVE.format(language=language)},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
            ]},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=90)
    return _extract_content(result)


def fix_code_with_screenshot(img: Image.Image, language: str = "python") -> str:
    """
    截图错误代码 → 调用视觉模型 → 返回原始文本响应
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)
    b64_img = _image_to_base64(img)

    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_FIX},
            {"role": "user", "content": [
                {"type": "text", "text": USER_PROMPT_FIX.format(language=language)},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
            ]},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=90)
    return _extract_content(result)


def chat_text(system_prompt: str, user_message: str, model: str = "") -> str:
    """
    纯文本对话
    """
    endpoint, api_key, default_model, _ = config.get_api_config()
    model = model or default_model
    url = _build_url(endpoint)

    body = {
        "model": model,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=60)
    return _extract_content(result)


# ─────────────────────────────────────────────
#  响应解析：分离代码块和解题思路
# ─────────────────────────────────────────────
import re


def parse_code_from_response(text: str) -> tuple[str, str]:
    """
    从 LLM 返回文本中分离代码块和解题思路。
    返回 (code, solution)
    """
    if not text:
        return "", ""

    # 提取所有 code block
    code_blocks = re.findall(r"```[\w]*\n(.*?)```", text, re.DOTALL)
    code = "\n\n".join(b.strip() for b in code_blocks) if code_blocks else ""

    # 去掉 code block 后剩余文本作为思路
    solution = re.sub(r"```[\w]*\n.*?```", "", text, flags=re.DOTALL).strip()

    # 如果完全没有 code block，整段作为 solution
    if not code:
        solution = text.strip()

    return code, solution
