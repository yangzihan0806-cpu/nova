"""
test_key.py —— 检查 key 能不能用。
运行: 双击 test.bat, 或在终端里运行  python test_key.py
"""

from ai import DEFAULT_MODEL, ask, key_is_set

if not key_is_set():
    print("[X] .env 里还没有填 key。用记事本打开 .env, 把 OPENROUTER_API_KEY= 后面换成真的 key。")
else:
    print(f"正在用模型 {DEFAULT_MODEL} 测试...")
    try:
        print("[OK] AI 回答:", ask("Reply with exactly: Hello NOVA!"))
    except Exception as e:
        print("[X] 调用失败:", e)
        print("常见原因: key 复制不完整 / 额度用完 / 模型名写错 / 网络问题")
