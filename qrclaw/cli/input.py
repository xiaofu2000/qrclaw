"""
用户输入模块

负责 prompt_toolkit 键绑定和读取用户输入。
"""
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

_bindings = KeyBindings()


@_bindings.add("escape", "enter")  # Alt+Enter 或 Esc+Enter 换行
def _newline(event):
    event.current_buffer.insert_text("\n")


def get_input(session_id: str) -> str:
    """读取一行用户输入，Enter 提交，Alt+Enter 换行。"""
    prompt_session = PromptSession(key_bindings=_bindings)
    return prompt_session.prompt(
        f"[{session_id}] > ",
        multiline=False,
    )
