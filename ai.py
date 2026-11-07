"""
ai.py —— 所有和 AI 打交道的代码都在这里。

其他文件只要:
    from ai import ask
    answer = ask("你的问题")
就能拿到 AI 的回答。
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # 读取同目录下的 .env 文件

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")

# OpenRouter 兼容 OpenAI 的接口, 所以直接用 OpenAI 的 SDK, 只是把地址换成 OpenRouter
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=API_KEY or "missing")


def key_is_set() -> bool:
    """检查 .env 里是否填了真的 key。"""
    return API_KEY.startswith("sk-or-") and "paste" not in API_KEY


def ask(prompt: str, model: str = DEFAULT_MODEL, system: str = "You are a helpful assistant.") -> str:
    """问一个问题, 返回完整回答(字符串)。最简单的用法。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


def stream_chat(messages: list[dict], model: str = DEFAULT_MODEL):
    """
    多轮对话 + 流式输出(回答一个字一个字地出来)。
    messages 的格式: [{"role": "user"/"assistant"/"system", "content": "..."}, ...]
    这是一个 generator, 每次 yield 一小段文字。
    """
    stream = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
