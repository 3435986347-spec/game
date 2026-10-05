"""着法白名单校验：讲解文字里出现的每一步中文着法，都必须在 allowed_moves_cn 中。

大模型（尤其是便宜的模型）最常见的错误是编造着法：说出局面里根本走不了、或引擎没给出的着法。
做法：用正则找出文本里所有像中文着法的片段，逐个和白名单比较。
比较时统一繁简体（車馬砲進）、全角数字，但保留红黑的写法差别：红方用中文数字、黑方用阿拉伯数字，
「马二进三」和「马2进3」是两步不同的棋。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

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


def _texts(value) -> list[str]:
    """输出里的全部文字（字符串字段和字符串列表）。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def unknown_moves(output: BaseModel, allowed_keys: set[str]) -> list[str]:
    """讲解中出现、但不在白名单里的着法（去重，保持出现顺序）。"""
    out: list[str] = []
    for text in _texts(output.model_dump()):
        for found in find_moves(text):
            if move_key(found) not in allowed_keys and found not in out:
                out.append(found)
    return out


def problems(output: BaseModel, allowed_keys: set[str]) -> list[str]:
    """讲解不合格的原因；合格时返回空列表。"""
    issues = []
    data = output.model_dump()
    first = next(iter(data.values()), "")
    if isinstance(first, str) and not first.strip():
        issues.append(f"{next(iter(data))} 是空的")
    unknown = unknown_moves(output, allowed_keys)
    if unknown:
        issues.append(
            f"提到了 allowed_moves_cn 之外的着法：{'、'.join(unknown)}。"
            "只能使用 allowed_moves_cn 里的着法，并且写法要完全一致"
            "（红方用中文数字，黑方用阿拉伯数字）"
        )
    return issues
