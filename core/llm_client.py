"""
LLM 客户端模块
用 raw HTTP 请求调用 OpenAI 兼容 API（支持 vision / 截图识别）
彻底绕过 openai SDK 版本差异问题
"""

import json
import re
import base64
import io
import time
import concurrent.futures
from PIL import Image
import config
from core.op_log import op_logger

# ── requests 导入（带降级） ──
try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    from urllib3.util import Timeout as Urllib3Timeout
    _HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    import ssl
    _HAS_REQUESTS = False
    Urllib3Timeout = None


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


def _raw_post(url: str, api_key: str, body: dict, timeout: int = 120) -> dict:
    """
    发送 raw HTTP POST，返回解析后的 JSON dict。
    timeout: 总超时秒数（connect + read 共享此预算）。
    优先使用 requests（真正的超时控制），降级到 urllib。
    """
    if _HAS_REQUESTS:
        return _raw_post_requests(url, api_key, body, timeout)
    else:
        return _raw_post_urllib(url, api_key, body, timeout)


def _raw_post_requests(url: str, api_key: str, body: dict, timeout: int) -> dict:
    """使用 requests 库发送 POST — 用 urllib3.util.Timeout 实现真正的总超时"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    model = body.get("model", "?")
    # ── 核心修复：使用 urllib3.util.Timeout(total=...) 实现总超时 ──
    # timeout=(connect, read) 的 read 只是"两次数据接收的间隔超时"
    # 如果服务器慢慢滴答数据（如推理模型 thinking），read_timeout 永远不触发
    # 而 Timeout(total=120) 是真正的"从请求开始到结束的总耗时上限"
    # 无论服务器怎么滴答数据，超过 total 后一定中断
    if Urllib3Timeout is not None:
        req_timeout = Urllib3Timeout(total=timeout, connect=15, read=timeout)
    else:
        # fallback：没有 urllib3（极罕见），用 tuple
        req_timeout = (15, timeout)

    _t0 = time.time()
    # 记录请求日志
    _msg_count = len(body.get("messages", []))
    _has_image = any("image_url" in str(m) for m in body.get("messages", []))
    op_logger.log({
        "op": "LLM请求",
        "model": model,
        "request_summary": f"msg数={_msg_count}  含图={_has_image}  body≈{len(json.dumps(body, ensure_ascii=False)):,}B",
        "status": "发送中",
        "elapsed": "",
    })

    try:
        resp = requests.post(
            url,
            json=body,
            headers=headers,
            timeout=req_timeout,
            verify=False,
        )
        _elapsed = time.time() - _t0
        print(f"[LLM] HTTP {resp.status_code}  耗时={_elapsed:.1f}s  len={len(resp.content)}B", flush=True)
        if resp.status_code != 200:
            try:
                err_json = resp.json()
                _err_msg = json.dumps(err_json, ensure_ascii=False)[:500]
            except json.JSONDecodeError:
                _err_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
            op_logger.log({
                "op": "LLM响应",
                "model": model,
                "elapsed": f"{_elapsed:.1f}s",
                "status": f"HTTP {resp.status_code}",
                "response_summary": _err_msg[:300],
                "error": _err_msg,
            })
            raise RuntimeError(_err_msg)
        _result = resp.json()
        # 记录成功日志
        _content_preview = _extract_content(_result)[:500]
        op_logger.log({
            "op": "LLM响应",
            "model": model,
            "elapsed": f"{_elapsed:.1f}s",
            "status": "成功",
            "response_summary": _content_preview,
        })
        return _result
    except requests.exceptions.Timeout:
        _elapsed = time.time() - _t0
        print(f"[LLM] 请求超时！耗时={_elapsed:.1f}s", flush=True)
        op_logger.log({
            "op": "LLM响应",
            "model": model,
            "elapsed": f"{_elapsed:.1f}s",
            "status": "超时",
            "error": f"总超时 {timeout}s（urllib3 Timeout）",
        })
        raise TimeoutError(f"请求超时（{timeout}s），请检查网络或换更快的模型")
    except requests.exceptions.ConnectionError as e:
        _elapsed = time.time() - _t0
        print(f"[LLM] 连接失败！耗时={_elapsed:.1f}s  error={e}", flush=True)
        op_logger.log({
            "op": "LLM响应",
            "model": model,
            "elapsed": f"{_elapsed:.1f}s",
            "status": "连接失败",
            "error": str(e)[:300],
        })
        raise ConnectionError(f"连接失败，请检查 Endpoint：{e}")


def _raw_post_urllib(url: str, api_key: str, body: dict, timeout: int) -> dict:
    """降级方案：使用 urllib（超时控制不精确）"""
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


def _image_to_base64(img: Image.Image, max_px: int = 1920 * 1080) -> str:
    """将 PIL Image 转为 base64 — 超大图自动缩放，防止请求体过大导致 API 卡死"""
    if img.width * img.height > max_px:
        ratio = (max_px / (img.width * img.height)) ** 0.5
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        print(f"[IMG] 缩放 {img.width}×{img.height} → {new_w}×{new_h}", flush=True)
    buf = io.BytesIO()
    # 用 JPEG 而非 PNG 减小体积（截图不需要无损）
    img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    print(f"[IMG] base64 长度={len(b64):,} chars  ≈{len(b64)*3//4:,}B", flush=True)
    return b64


def _build_vision_message(text: str, img_b64: str) -> list[dict]:
    """构建包含图片的 user message content 数组"""
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
    ]


def _build_multi_vision_message(text: str, img_b64_list: list[str]) -> list[dict]:
    """构建包含多张图片的 user message content 数组（OpenAI vision 多图格式）"""
    content: list[dict] = [{"type": "text", "text": text}]
    for b64 in img_b64_list:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return content


def _merge_images_vertically(imgs: list[Image.Image], max_width: int = 1280) -> Image.Image:
    """
    将多张 PIL Image 垂直拼合成一张大图（等宽缩放后拼接）。
    用于不支持多图的模型降级方案。
    """
    if not imgs:
        raise ValueError("No images to merge")
    if len(imgs) == 1:
        return imgs[0]

    # 统一宽度为最小宽度，但不超过 max_width
    min_w = min(img.width for img in imgs)
    target_w = min(min_w, max_width)

    resized = []
    for img in imgs:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        ratio = target_w / img.width
        new_h = int(img.height * ratio)
        resized.append(img.resize((target_w, new_h), Image.LANCZOS))

    total_h = sum(r.height for r in resized) + (len(resized) - 1) * 8
    merged = Image.new("RGB", (target_w, total_h), (240, 240, 240))
    y = 0
    for r in resized:
        merged.paste(r, (0, y))
        y += r.height + 8

    return merged


# ─────────────────────────────────────────────
#  提示词模板（默认值在 config.py 中，可通过设置界面修改）
# ─────────────────────────────────────────────
def _sys_solve():      return config.get_prompt("system_solve")
def _sys_qa():         return config.get_prompt("system_qa")
def _user_qa_solve():  return config.get_prompt("user_qa_solve")
def _sys_debug():      return config.get_prompt("system_debug")

# 不可配置的内置模板（包含格式化占位符）
USER_PROMPT_SOLVE_LEETCODE = """请处理截图中的代码题目。

【关键】content 字段必须逐字复制截图中的所有原文，禁止用自己的话改写。

代码要求：
- LeetCode 风格（class Solution + 方法）
- 语言：{language}

按 JSON Schema 返回结果。"""

USER_PROMPT_SOLVE_ACM = """请处理截图中的代码题目。

【关键】content 字段必须逐字复制截图中的所有原文，禁止用自己的话改写。

代码要求：
- ACM 风格（input() + print()）
- 语言：{language}

按 JSON Schema 返回结果。"""

USER_PROMPT_DEBUG = """下面是题目截图和我的当前代码（语言：{language}）。

{current_code}

请检查代码是否有错误，按 JSON Schema 返回结果。"""


# ─────────────────────────────────────────────
#  公开 API — 基础功能（保留旧接口兼容）
# ─────────────────────────────────────────────
def solve_with_screenshot(img: Image.Image, language: str = "python",
                         sub_mode: str = "leetcode") -> dict:
    """
    截图 → 调用视觉模型 → 返回结构化 JSON dict
    sub_mode: "leetcode" 或 "acm"，影响代码风格
    返回格式: {title, content, thinking_process, code, programming_language}
    解析失败时返回 raw 字段作为兜底
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)
    b64_img = _image_to_base64(img)

    template = USER_PROMPT_SOLVE_ACM if sub_mode == "acm" else USER_PROMPT_SOLVE_LEETCODE
    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _sys_solve()},
            {"role": "user", "content": _build_vision_message(
                template.format(language=language), b64_img
            )},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=120)
    raw_text = _extract_content(result)
    return _parse_json_safe(raw_text)


def solve_with_multiple_screenshots(imgs: list[Image.Image], language: str = "python",
                                    sub_mode: str = "leetcode") -> dict:
    """
    多图解题：将多张截图同时发送给视觉模型（优先多图方案，降级到拼合方案）。
    当只有 1 张图时直接调 solve_with_screenshot，保持兼容。
    返回格式同 solve_with_screenshot。
    """
    if not imgs:
        raise ValueError("至少需要 1 张截图")
    if len(imgs) == 1:
        return solve_with_screenshot(imgs[0], language=language, sub_mode=sub_mode)

    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)

    template = USER_PROMPT_SOLVE_ACM if sub_mode == "acm" else USER_PROMPT_SOLVE_LEETCODE
    user_text = (
        f"以下是题目的 {len(imgs)} 张截图（按顺序拼接成完整题目）。\n"
        + template.format(language=language)
    )

    # 优先尝试多图方案（OpenAI vision 支持）
    b64_list = [_image_to_base64(img) for img in imgs]
    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _sys_solve()},
            {"role": "user", "content": _build_multi_vision_message(user_text, b64_list)},
        ],
    }

    try:
        result = _raw_post(url, api_key, body, timeout=120)
        raw_text = _extract_content(result)
        return _parse_json_safe(raw_text)
    except Exception:
        # 降级方案：拼合成一张大图后单图发送
        merged = _merge_images_vertically(imgs)
        return solve_with_screenshot(merged, language=language, sub_mode=sub_mode)


def qa_solve_with_multiple_screenshots(imgs: list[Image.Image]) -> dict:
    """
    QA 模式多图解题，优先多图方案，降级拼合。
    返回格式同 qa_solve_with_screenshot。
    """
    if not imgs:
        raise ValueError("至少需要 1 张截图")
    if len(imgs) == 1:
        return qa_solve_with_screenshot(imgs[0])

    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)

    user_text = (
        f"以下是 {len(imgs)} 张截图（按顺序构成完整内容）。\n"
        + _user_qa_solve()
    )
    b64_list = [_image_to_base64(img) for img in imgs]
    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _sys_qa()},
            {"role": "user", "content": _build_multi_vision_message(user_text, b64_list)},
        ],
    }

    try:
        result = _raw_post(url, api_key, body, timeout=120)
        raw_text = _extract_content(result)
        return _parse_json_safe(raw_text)
    except Exception:
        merged = _merge_images_vertically(imgs)
        return qa_solve_with_screenshot(merged)


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

    result = _raw_post(url, api_key, body, timeout=120)
    return _extract_content(result)


# ─────────────────────────────────────────────
#  公开 API — 新功能：纠错 / 对话 / QA
# ─────────────────────────────────────────────

def debug_code(screenshot_b64: str, current_code: str, language: str = "python") -> dict:
    """
    一键纠错：发送题目截图 + 当前代码 → 返回结构化 JSON
    返回: {error_analysis, fixed_code, modifications}
    解析失败时返回 raw 字段作为兜底
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)

    user_text = USER_PROMPT_DEBUG.format(
        language=language,
        current_code=current_code if current_code else "（无）"
    )

    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _sys_debug()},
            {"role": "user", "content": _build_vision_message(user_text, screenshot_b64)},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=120)
    raw_text = _extract_content(result)
    return _parse_json_safe(raw_text)


def chat_with_context(screenshot_b64: str, current_code: str,
                      user_input: str, language: str = "python",
                      chat_history: list[dict] | None = None) -> dict:
    """
    智能对话：发送截图 + 最新代码 + 用户输入 + 对话历史 → 返回结构化 dict
    返回: {"ai_analysis": "AI回复文本", "ai_code": "修改后的代码或空字符串"}
    """
    endpoint, api_key, model, vision_model = config.get_api_config()
    url = _build_url(endpoint)

    # 构建系统提示词（含代码上下文）
    sys_text = (
        "你是一个编程面试辅导助手。用户正在做一道编程题。\n"
        "你可以看到题目截图和用户当前的代码。请基于这些上下文回答用户的问题。\n"
        "如果用户的问题涉及代码修改，请在回复中给出修改后的完整代码。\n"
        "回答简洁直接。"
    )
    if current_code:
        sys_text += f"\n\n用户当前代码（{language}）：\n```{language}\n{current_code}\n```"

    messages = [{"role": "system", "content": sys_text}]

    # 添加最近对话历史（最近 6 轮，即 12 条消息）
    if chat_history:
        recent = chat_history[-12:] if len(chat_history) > 12 else chat_history
        for msg in recent:
            messages.append({"role": msg["role"], "content": msg["content"]})

    # 添加当前用户消息（含截图）
    use_vision = vision_model or model
    messages.append({
        "role": "user",
        "content": _build_vision_message(user_input, screenshot_b64),
    })

    body = {
        "model": use_vision,
        "max_tokens": 2048,
        "messages": messages,
    }

    result = _raw_post(url, api_key, body, timeout=120)
    raw_text = _extract_content(result)

    # 尝试从回复中提取代码块作为 ai_code
    ai_code = _extract_code_from_text(raw_text)
    return {"ai_analysis": raw_text, "ai_code": ai_code}


def _extract_code_from_text(text: str) -> str:
    """从 LLM 回复文本中提取第一个代码块，没有则返回空字符串"""
    if not text:
        return ""
    code_blocks = re.findall(r"```[\w]*\n(.*?)```", text, re.DOTALL)
    if code_blocks:
        return "\n\n".join(b.strip() for b in code_blocks)
    return ""


def qa_chat(screenshot_b64: str, user_input: str,
            chat_history: list[dict] | None = None) -> dict:
    """
    QA 模式对话：仅发送截图 + 用户输入 → 返回结构化 dict
    返回: {"ai_analysis": "AI回复文本", "ai_code": ""}
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)

    messages = [{"role": "system", "content": _sys_qa()}]

    # 添加最近对话历史
    if chat_history:
        recent = chat_history[-12:] if len(chat_history) > 12 else chat_history
        for msg in recent:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({
        "role": "user",
        "content": _build_vision_message(user_input, screenshot_b64),
    })

    body = {
        "model": vision_model,
        "max_tokens": 2048,
        "messages": messages,
    }

    result = _raw_post(url, api_key, body, timeout=120)
    raw_text = _extract_content(result)
    return {"ai_analysis": raw_text, "ai_code": ""}


def qa_solve_with_screenshot(img: Image.Image) -> dict:
    """
    QA 模式：截图 → 视觉模型 → 返回结构化 JSON dict
    返回格式: {title, content, thinking_process, code, programming_language}
    """
    endpoint, api_key, _, vision_model = config.get_api_config()
    url = _build_url(endpoint)
    b64_img = _image_to_base64(img)

    body = {
        "model": vision_model,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _sys_qa()},
            {"role": "user", "content": _build_vision_message(
                _user_qa_solve(), b64_img
            )},
        ],
    }

    result = _raw_post(url, api_key, body, timeout=120)
    raw_text = _extract_content(result)
    return _parse_json_safe(raw_text)


# ─────────────────────────────────────────────
#  响应解析
# ─────────────────────────────────────────────


def _parse_json_safe(text: str) -> dict:
    """
    安全解析 LLM 返回的 JSON 文本。
    1. 尝试直接解析
    2. 尝试提取 markdown code block 中的 JSON
    3. 尝试查找第一个 { 和最后一个 } 之间的内容
    4. 全部失败则返回 {"raw": text}
    """
    if not text:
        return {"raw": ""}

    text = text.strip()

    # 尝试1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试2：提取 markdown code block 中的 JSON
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试3：找到第一个 { 和最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    # 兜底
    return {"raw": text}


def parse_code_from_response(text: str) -> tuple[str, str]:
    """
    从 LLM 返回文本中分离代码块和解题思路（旧格式兼容）
    返回 (code, solution)
    """
    if not text:
        return "", ""

    # 提取所有 code block
    code_blocks = re.findall(r"```[\w]*\n(.*?)```", text, re.DOTALL)
    code = "\n\n".join(b.strip() for b in code_blocks) if code_blocks else ""

    # 去掉 code block 后剩余文本作为思路
    solution = re.sub(r"```[\w]*\n.*?```", "", text, flags=re.DOTALL).strip()

    if not code:
        solution = text.strip()

    return code, solution
