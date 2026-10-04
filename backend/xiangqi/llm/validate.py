"""着法白名单校验：讲解文字里出现的每一步中文着法，都必须在 allowed_moves_cn 中。

大模型（尤其是便宜的模型）最常见的错误是编造着法：说出局面里根本走不了、或引擎没给出的着法。
做法：用正则找出文本里所有像中文着法的片段，逐个和白名单比较。
比较时统一繁简体（車馬砲進）、全角数字，但保留红黑的写法差别：红方用中文数字、黑方用阿拉伯数字，
「马二进三」和「马2进3」是两步不同的棋。
"""

from __future__ import annotations

import re

from .base import Explanation

_PIECES = "车車俥马馬傌炮砲包相象仕士帅帥将將兵卒"
_NUM = "一二三四五六七八九1-9１-９"
CN_MOVE = re.compile(
    rf"(?:[前中后後][{_PIECES}]|[{_PIECES}][{_NUM}]|[前中后後][{_NUM}])"
    rf"[进進退平][{_NUM}]"
)

_KEY_TABLE = str.maketrans(
    {
        **dict.fromkeys("車俥", "车"),
        **dict.fromkeys("馬傌", "马"),
        **dict.fromkeys("砲包", "炮"),
        "帥": "帅",
        "將": "将",
        "進": "进",
        "後": "后",
        **{c: str(i) for i, c in enumerate("０１２３４５６７８９")},
    }
)


def move_key(text: str) -> str:
    """比较用的键：统一繁简体和全角数字。"""
    return re.sub(r"\s+", "", text).translate(_KEY_TABLE)


def find_moves(text: str) -> list[str]:
    return CN_MOVE.findall(text)


def unknown_moves(explanation: Explanation, allowed_keys: set[str]) -> list[str]:
    """讲解中出现、但不在白名单里的着法（去重，保持出现顺序）。"""
    texts = [explanation.headline, explanation.why, explanation.better, explanation.principle]
    texts += explanation.tags
    out: list[str] = []
    for text in texts:
        for found in find_moves(text):
            if move_key(found) not in allowed_keys and found not in out:
                out.append(found)
    return out


def problems(explanation: Explanation, allowed_keys: set[str]) -> list[str]:
    """讲解不合格的原因；合格时返回空列表。"""
    issues = []
    if not explanation.headline.strip():
        issues.append("headline 是空的")
    unknown = unknown_moves(explanation, allowed_keys)
    if unknown:
        issues.append(
            f"提到了 allowed_moves_cn 之外的着法：{'、'.join(unknown)}。"
            "只能使用 allowed_moves_cn 里的着法，并且写法要完全一致"
            "（红方用中文数字，黑方用阿拉伯数字）"
        )
    return issues
