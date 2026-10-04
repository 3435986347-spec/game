"""棋谱文件解析：编码识别、按对局切分、PGN 标签与着法正文。

只做文本层面的解析，不检查着法是否合法（那是 importer.resolve_game 的事）。
支持的着法写法：ICCS（H2-E2、h2e2）、中文纵线记谱（炮二平五，可连写不加空格）、WXF（C2.5、H8+7）。
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class RawGame:
    headers: dict[str, str] = field(default_factory=dict)  # PGN 标签，键名保持原样
    # 着法原文，已去掉回合编号、注释、变着和结果标记
    moves: list[str] = field(default_factory=list)
    result: str | None = None  # 着法正文末尾的结果标记
    index: int = 1  # 在文件中是第几局，从 1 开始


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------


def decode_bytes(data: bytes) -> str:
    """UTF-8（有无 BOM）、UTF-16（有 BOM），否则按 GB18030（兼容 GBK / GB2312）解码。
    几个文件直接拼接时中间也会有 BOM，一并去掉。"""
    if data.startswith(codecs.BOM_UTF8):
        text = data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
    return text.replace("\ufeff", "")


# ---------------------------------------------------------------------------
# 切分对局与标签
# ---------------------------------------------------------------------------

# 值按贪婪匹配：有的软件不转义值里的双引号，如 [Event "第一届"棋王"赛"]
_TAG_RE = re.compile(r'^\[\s*([A-Za-z0-9_]+)\s+"(.*)"\s*\]$')


def split_games(text: str) -> list[RawGame]:
    return list(iter_games(text))


def iter_games(text: str) -> Iterator[RawGame]:
    """逐局产出。每局以若干行标签（[Tag "value"]）开始，后面是着法正文；
    没有任何标签的文本视为一局。"""
    headers: dict[str, str] = {}
    body: list[str] = []
    in_comment = False  # 跨行的 {注释} 里出现的 [ 不是标签
    index = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        tag = None if in_comment else _TAG_RE.match(stripped)
        if tag:
            # 上一局的正文已经结束；或者同一个标签又出现了——上一局只有标签、没有着法
            if any(part.strip() for part in body) or tag.group(1) in headers:
                for game in _make_games(headers, body, index):
                    index = game.index
                    yield game
                headers, body = {}, []
            headers[tag.group(1)] = _unescape(tag.group(2))
            continue
        body.append(line)
        in_comment = _comment_state(line, in_comment)
    if headers or any(part.strip() for part in body):
        yield from _make_games(headers, body, index)


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value).strip()


def _comment_state(line: str, in_comment: bool) -> bool:
    """这一行结束时是否还在 {注释} 里。规则与 _strip_annotations 一致：; 之后是行注释。"""
    for ch in line:
        if in_comment:
            in_comment = ch != "}"
        elif ch == "{":
            in_comment = True
        elif ch == ";":
            break
    return in_comment


def _make_games(headers: dict[str, str], body: list[str], last_index: int) -> Iterator[RawGame]:
    """一段正文通常是一局；结果标记（1-0、* 等）之后如果还有着法，那是下一局（没有标签）。"""
    segments = tokenize_segments("\n".join(body)) or [([], None)]
    for i, (moves, result) in enumerate(segments):
        yield RawGame(
            headers=headers if i == 0 else {}, moves=moves, result=result, index=last_index + i + 1
        )


# ---------------------------------------------------------------------------
# 着法正文
# ---------------------------------------------------------------------------

_RESULTS = {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2-1/2", "½-½": "1/2-1/2", "*": "*"}
# 有的软件在着法后面直接写中文结果
_RESULT_WORDS = {
    "红胜": "1-0", "红先胜": "1-0", "红方胜": "1-0",
    "黑负": "1-0", "黑方负": "1-0", "黑先负": "1-0",
    "黑胜": "0-1", "黑先胜": "0-1", "黑方胜": "0-1",
    "红负": "0-1", "红方负": "0-1", "红先负": "0-1",
    "和": "1/2-1/2", "和棋": "1/2-1/2", "和局": "1/2-1/2", "平局": "1/2-1/2",
    "红先和": "1/2-1/2", "黑先和": "1/2-1/2",
}  # fmt: skip
RESULT_TOKENS = {**_RESULTS, **_RESULT_WORDS}  # 结果标记 → 1-0 / 0-1 / 1/2-1/2 / *

_PIECE = "车車俥伡马馬傌炮砲包相象仕士帅帥将將兵卒"
_NUM = "一二三四五六七八九1-9１-９"
_CN_MOVE = re.compile(
    rf"(?:[前中后後][{_PIECE}][{_NUM}]?|[前中后後][{_NUM}]|[一二三四五][兵卒]|[{_PIECE}][{_NUM}])"
    rf"[进進退平][{_NUM}]"
)
_ICCS = re.compile(r"[A-Ia-i][0-9]-?[A-Ia-i][0-9]")
_WXF = re.compile(r"(?:[+-][KAEBHNRCPkaebhnrcp]|[KAEBHNRCPkaebhnrcp][1-9+-])[+\-.=][1-9]")
_MOVE_NUMBER = re.compile(r"\d+\s*[.．、]+")
_SEPARATORS = str.maketrans({c: " " for c in "，,；、。"})
_QUALITY_MARKS = "!?！？"  # 着法后的好坏标记，如 炮二平五!?


def tokenize_movetext(text: str) -> tuple[list[str], str | None]:
    """着法正文 → (着法列表, 末尾的结果标记)。
    无法识别的片段原样保留在着法列表里，由后续步骤报错。"""
    segments = tokenize_segments(text)
    moves = [m for seg, _ in segments for m in seg]
    return moves, segments[-1][1] if segments else None


def tokenize_segments(text: str) -> list[tuple[list[str], str | None]]:
    """按结果标记把正文切成若干局：[(着法列表, 结果标记), ...]。"""
    text = _strip_annotations(text).translate(_SEPARATORS)
    segments: list[tuple[list[str], str | None]] = []
    moves: list[str] = []
    for token in text.split():
        token = token.strip().rstrip(_QUALITY_MARKS)
        if not token:
            continue
        result = RESULT_TOKENS.get(token)
        if result is not None:
            if moves or not segments:
                segments.append((moves, result))
                moves = []
            # 否则是紧跟在结果后面的又一个结果标记（如「1-0 红胜」），不是新的一局
            continue
        if _MOVE_NUMBER.fullmatch(token) or token.isdigit():
            continue
        rest = _MOVE_NUMBER.sub(" ", token, count=1) if _MOVE_NUMBER.match(token) else token
        for part in rest.split():
            part = part.rstrip(_QUALITY_MARKS)
            if _ICCS.fullmatch(part) or _WXF.fullmatch(part):
                moves.append(part)
            else:
                moves.extend(_split_chinese(part))
    if moves or not segments:
        segments.append((moves, None))
    return [seg for seg in segments if seg[0] or seg[1]]


def _split_chinese(token: str) -> list[str]:
    """一个片段里可能连写了多步中文着法（如「炮二平五马8进7」）；不能完整切分时原样返回。"""
    pieces = [p for p in _MOVE_NUMBER.split(token) if p]
    out: list[str] = []
    for piece in pieces:
        found = [m.group(0) for m in _CN_MOVE.finditer(piece)]
        if found and "".join(found) == piece:
            out.extend(found)
        else:
            return [token]
    return out


def _strip_annotations(text: str) -> str:
    """去掉 {注释}、; 行注释、(变着)（可嵌套，含全角括号）和 $NAG。"""
    out: list[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            end = text.find("}", i + 1)
            i = n if end < 0 else end + 1
            out.append(" ")
        elif ch == ";":
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif ch in "(（":
            depth += 1
            i += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
            i += 1
            out.append(" ")
        elif depth > 0:
            i += 1
        elif ch == "$":
            i += 1
            while i < n and text[i].isdigit():
                i += 1
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)
