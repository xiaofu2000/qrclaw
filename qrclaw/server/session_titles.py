"""首轮对话完成后为会话生成简短标题。

未生成或生成失败时，列表继续使用第一句话的前 48 个字作为标题；用户改过名的会话不会被覆盖。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from threading import Thread

from qrclaw.logger import get_logger
from qrclaw.memory.context.session import Session

logger = get_logger("qrclaw.server.session_titles")

TITLE_PROMPT = (
    "用不超过 16 个汉字概括下面这段对话要做的事，作为会话列表里的标题。"
    "只输出标题本身，不加引号、书名号、句号或任何解释。"
)
MAX_TITLE_LENGTH = 24


def _first_text(messages: list[dict], role: str) -> str:
    """取某个角色的第一段正文，跳过工具调用和执行汇报。"""
    for message in messages:
        if (message.get("role") == role and message.get("kind") != "execution_report" and not message.get("tool_calls")
                and isinstance(message.get("content"), str) and message["content"].strip()):
            return " ".join(message["content"].split())
    return ""


def clean_title(raw: str) -> str:
    """去掉模型常带的引号、前缀和结尾标点，只保留第一行。"""
    line = next((part.strip() for part in raw.splitlines() if part.strip()), "")
    line = re.sub(r"^(标题|会话标题)\s*[:：]\s*", "", line)
    # 引号与结尾标点可能交替出现（如「标题」。），反复去除直到不再变化。
    previous = None
    while previous != line:
        previous = line
        line = line.strip(" \"'“”‘’「」『』《》【】`*#").rstrip("。.!！?？,，;；")
    return line[:MAX_TITLE_LENGTH]


def suggest_title(messages: list[dict], llm=None) -> str | None:
    """根据第一问和第一段回复生成标题；内容不足或模型失败时返回 None。"""
    question, reply = _first_text(messages, "user"), _first_text(messages, "assistant")
    if not question or not reply:
        return None
    if llm is None:
        from qrclaw.llm_service import get_llm_service
        llm = get_llm_service()
    try:
        response = llm.chat([
            {"role": "system", "content": TITLE_PROMPT},
            {"role": "user", "content": f"用户：{question[:600]}\n助手：{reply[:600]}"},
        ], temperature=0.2)
    except Exception as exc:
        logger.warning("会话标题生成失败：%s", exc)
        return None
    title = clean_title(response.content or "")
    return title or None


def generate_title(sessions_dir: Path, session_id: str, llm=None) -> str | None:
    """读取最新会话，没有标题时生成并保存；返回实际保存的标题。"""
    session = Session(sessions_dir, session_id)
    if session.title or not session._path.exists():
        return None
    title = suggest_title(session.messages, llm)
    if not title:
        return None
    try:
        return title if session.set_title(title, only_if_unset=True) else None
    except FileNotFoundError:
        return None


def schedule_title(sessions_dir: Path, session_id: str) -> None:
    """后台生成，不拖慢本轮回复的收尾；环境变量 QRCLAW_AUTO_TITLE=0 时关闭（测试默认关闭，避免真实模型调用）。"""
    if os.environ.get("QRCLAW_AUTO_TITLE", "1") == "0":
        return
    Thread(target=lambda: generate_title(sessions_dir, session_id), name=f"title-{session_id}", daemon=True).start()
