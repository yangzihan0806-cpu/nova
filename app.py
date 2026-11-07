"""
app.py —— 网页界面 (Streamlit)。
启动: 双击 run.bat, 或在终端里运行  streamlit run app.py
浏览器会自动打开 http://localhost:8501
"""

import streamlit as st

from ai import DEFAULT_MODEL, key_is_set, stream_chat

st.set_page_config(page_title="NOVA Starter", page_icon="🤖")
st.title("🤖 NOVA Starter Chat")

# ---------- 检查 key ----------
if not key_is_set():
    st.error(
        "还没有设置 API key。\n\n"
        "用记事本打开项目文件夹里的 .env 文件, 把 OPENROUTER_API_KEY= 后面换成你们拿到的 key, "
        "保存后刷新这个网页。"
    )
    st.stop()

# ---------- 侧边栏设置 ----------
with st.sidebar:
    st.header("设置")
    model = st.text_input("Model", value=DEFAULT_MODEL, help="在 openrouter.ai/models 查模型名")
    system_prompt = st.text_area(
        "System prompt (给 AI 的角色设定)",
        value="You are a helpful assistant.",
        height=150,
    )
    if st.button("清空对话"):
        st.session_state.messages = []
        st.rerun()

# ---------- 对话记录 ----------
# st.session_state 在页面刷新之间保存数据, 用来记住聊天历史
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------- 用户输入 ----------
if prompt := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 每次都把 system prompt + 全部历史发给 AI (AI 本身不记得之前说过什么)
    full_messages = [{"role": "system", "content": system_prompt}] + st.session_state.messages

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(stream_chat(full_messages, model=model))
        except Exception as e:
            st.error(f"调用 AI 失败: {e}")
            st.session_state.messages.pop()  # 删掉这条没成功的提问
            st.stop()

    st.session_state.messages.append({"role": "assistant", "content": reply})
