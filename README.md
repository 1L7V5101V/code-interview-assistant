# 手撕代码助手

> 仿 Cuemate 的半透明桌面浮窗工具，截图识别代码题，调用 LLM 生成答案和解题思路。

## 功能

- 🖥️ **半透明浮窗**：置顶、可拖拽、可调节透明度
- 📸 **截图解题**：全屏截图 / 区域截图，自动调用 LLM 分析
- 🔧 **代码纠错**：截图错误代码，AI 指出问题并给出修正版本
- 📝 **历史记录**：自动保存每道题，支持上/下一题切换
- ⌨️ **全局快捷键**：`Ctrl+B` 显隐、`Ctrl+H` 截图、`Ctrl+[`/`]` 切换题目
- ⚙️ **灵活配置**：支持任意 OpenAI 兼容接口（GPT/DeepSeek/Gemini/Ollama...）

## 安装依赖

```bash
pip install pyqt6 pillow pyautogui openai keyboard
```

## 配置 API

启动后点击右上角 **齿轮按钮 ⚙️**，填写：

| 字段 | 说明 | 示例 |
|------|------|------|
| API Endpoint | OpenAI 格式接口地址 | `https://api.openai.com/v1` |
| API Key | 你的 API Key | `sk-...` |
| 文本模型 | 非视觉题用 | `gpt-4o` |
| 视觉模型 | 截图识别用（需支持 vision） | `gpt-4o` |
| 编程语言 | 代码语言 | `python` / `java` / `cpp` ... |

> 如果使用 Ollama 本地模型，Endpoint 填 `http://localhost:11434/v1`，API Key 填 `ollama`。

## 运行

双击 `run.bat`，或命令行运行：

```bash
python main.py
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+B` | 显示/隐藏浮窗 |
| `Ctrl+H` | 全屏截图解题 |
| `Ctrl+[` | 上一题 |
| `Ctrl+]` | 下一题 |
| `Ctrl+\` | 切换点击穿透（鼠标穿透窗口） |

## 注意事项

1. **API Key 必须配置**才能使用截图解题功能
2. **视觉模型**需要支持 vision（如 GPT-4o、Gemini 1.5 Pro 等）
3. **全局快捷键**需要管理员权限，如失效请以管理员身份运行
4. **屏幕共享隐身**：窗口在会议软件屏幕共享时是否可见，取决于会议软件的采集方式（建议在腾讯会议"流畅度优先"模式下使用）
