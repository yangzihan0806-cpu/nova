# NOVA Starter：Python + Streamlit + OpenRouter

一个已经能跑的 AI 聊天网页。比赛时在这个基础上改成你们自己的项目。

## 第一次使用（Windows，按顺序双击）

1. **`setup.bat`**：创建虚拟环境（`.venv`），安装依赖，生成 `.env` 文件，并用记事本打开它。
   - 如果提示找不到 Python，先去 https://www.python.org/downloads/ 安装，安装时勾选 **"Add python.exe to PATH"**。
2. 在打开的 `.env` 里，把 `sk-or-v1-paste-your-key-here` 换成主办方发的 key，然后保存。
3. **`test.bat`**：测试 key 能不能用。看到 `[OK] AI 回答: Hello NOVA!` 就说明成功了。
4. **`run.bat`**：启动网页，浏览器会自动打开 http://localhost:8501 。要停止的话，关掉那个黑色窗口。

改完代码后不用重启：保存文件，刷新网页就行。

## 文件说明

| 文件 | 作用 |
|---|---|
| `ai.py` | 所有调用 AI 的代码。`ask("问题")` 返回回答字符串；`stream_chat(messages)` 用于流式输出 |
| `app.py` | 网页界面（Streamlit），主要改这个文件 |
| `test_key.py` | 检查 key 是否可用 |
| `.env` | 存放 key 和默认模型名。**不要上传到 GitHub，也不要发给别人** |
| `requirements.txt` | 需要安装的 Python 包 |

## 怎么改成自己的项目

- **改 AI 的角色**：修改网页侧边栏里的 System prompt，或者改 `app.py` 里的默认值。
- **换模型**：修改 `.env` 里的 `OPENROUTER_MODEL`，模型名在 https://openrouter.ai/models 查。
- **在自己的逻辑里用 AI**：

  ```python
  from ai import ask
  summary = ask("把下面的笔记总结成 5 个要点:\n" + notes_text)
  ```

- **常用 Streamlit 组件**（文档：https://docs.streamlit.io ）：
  `st.file_uploader`（上传文件）、`st.button`、`st.selectbox`、`st.columns`（分栏）、`st.image`、`st.dataframe`。

## 常见问题

- **`streamlit` 不是内部或外部命令**：请用 `run.bat` 启动；如果在终端里手动启动，要先运行 `.venv\Scripts\activate`。
- **401 / Unauthorized**：key 填错了或者没复制完整。
- **402 / 额度不足**：key 的额度用完了，去问主办方。
- **model not found**：模型名写错了，去 openrouter.ai/models 复制正确的名字。
