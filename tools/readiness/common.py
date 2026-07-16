"""就绪度检查共享工具。"""

from __future__ import annotations


def text_between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    if not end:
        return tail
    if end not in tail:
        return tail
    return tail.split(end, 1)[0]
