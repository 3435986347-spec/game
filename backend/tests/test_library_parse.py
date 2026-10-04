"""棋谱解析与导入：decode_bytes、split_games、resolve_game。

只依据接口约定编写，用来独立检验实现。期望的 ICCS 坐标和中文记谱都用规则引擎核对过，
带 cn_of() 的用例在运行时还会用规则引擎再核对一遍测试数据本身。
"""

import random

import pytest

from xiangqi.core import START_FEN, Position, move_to_chinese, move_to_iccs, parse_iccs
from xiangqi.core.board import file_of
from xiangqi.library.importer import GameFormatError, ParsedGame, resolve_game
from xiangqi.library.parse import RawGame, decode_bytes, split_games

BLACK_START = START_FEN.replace(" w ", " b ")
# 红车 i5、i0 同在一路；黑马 e7、e6 同在 5 路
ROOKS_FEN = "4k4/9/4n4/4n4/8R/9/9/9/9/3K4R w - - 0 1"
ROOKS_FEN_BLACK = "4k4/9/4n4/4n4/8R/9/9/9/9/3K4R b - - 0 1"
# 红炮 h4、h2 同在二路
CANNONS_FEN = "3k5/9/9/9/9/7C1/9/7C1/9/4K4 w - - 0 1"
# 黑炮 h4、h2 同在 8 路（黑方的「前」是靠近红方的 h2）
CANNONS_FEN_BLACK = "4k4/9/9/9/9/7c1/9/7c1/9/3K5 b - - 0 1"
# 三个红兵在五路
PAWNS3_FEN = "3k5/9/4P4/4P4/4P4/9/9/9/9/4K4 w - - 0 1"
# 五路、七路各有两个红兵
PAWNS2X2_FEN = "3k5/9/9/2P1P4/2P1P4/9/9/9/9/4K4 w - - 0 1"
# 车帅对将，黑方先走（被将军）
ENDGAME_FEN = "4k4/9/9/9/9/9/9/9/4R4/5K3 b - - 0 1"

# 中炮过河车对屏风马，12 个回合
OPENING_CN = [
    "炮二平五", "马8进7", "马二进三", "车9平8", "车一平二", "卒7进1",
    "车二进六", "马2进3", "兵七进一", "炮8平9", "车二平三", "炮9退1",
    "马八进七", "士4进5", "马七进六", "炮9平7", "车三平四", "马7进8",
    "炮八平七", "车1平2", "车九平八", "炮2进4", "兵五进一", "象3进5",
]  # fmt: skip
OPENING_ICCS = [
    "h2e2", "h9g7", "h0g2", "i9h9", "i0h0", "g6g5",
    "h0h6", "b9c7", "c3c4", "h7i7", "h6g6", "i7i8",
    "b0c2", "d9e8", "c2d4", "i8g8", "g6f6", "g7h5",
    "b2c2", "a9b9", "a0b0", "b7b3", "e3e4", "c9e7",
]  # fmt: skip
CN4 = OPENING_CN[:4]
ICCS4 = OPENING_ICCS[:4]


def make_raw(moves, headers=None, result=None, index=1) -> RawGame:
    return RawGame(headers=dict(headers or {}), moves=list(moves), result=result, index=index)


def resolve(moves, fen=None) -> ParsedGame:
    return resolve_game(make_raw(moves, {} if fen is None else {"FEN": fen}))


def cn_of(fen: str, iccs_moves: list[str]) -> list[str]:
    """用规则引擎把一串 ICCS 着法转成标准中文记谱（核对测试数据用）。"""
    pos = Position.from_fen(fen)
    out = []
    for text in iccs_moves:
        move = parse_iccs(text)
        assert pos.is_legal(move), (fen, text)
        out.append(move_to_chinese(pos.board, move))
        pos.push(move)
    return out


def test_opening_data_matches_engine():
    assert cn_of(START_FEN, OPENING_ICCS) == OPENING_CN


# ---------------------------------------------------------------------------
# decode_bytes
# ---------------------------------------------------------------------------

SIMPLIFIED_TEXT = (
    '[Event "全国象棋个人赛"]\n'
    '[Red "黑龙江 郭莉萍"]\n'
    '[Black "上海 单霞丽"]\n'
    "\n"
    "1. 炮二平五 马8进7 2. 马二进三 车9平8 1-0\n"
)
# 繁体字、全角数字：GBK 有，GB2312 没有
TRADITIONAL_TEXT = '[Event "全國象棋個人賽"]\n\n1. 砲二平五 馬８進７ 2. 傌二進三 車９平８ *\n'
# 「䶮」在 GB18030 里有、GBK 里没有（CJK 扩展 A）
GB18030_TEXT = '[Red "刘䶮"]\n\n1. h2e2 h9g7 *\n'


def test_decode_ascii():
    text = '[Event "Test"]\n\n1. h2e2 h9g7 *\n'
    assert decode_bytes(text.encode("ascii")) == text


@pytest.mark.parametrize("text", [SIMPLIFIED_TEXT, TRADITIONAL_TEXT, GB18030_TEXT])
def test_decode_utf8_without_bom(text):
    # 中文的 UTF-8 字节不能被当成 GB18030 解码
    assert decode_bytes(text.encode("utf-8")) == text


@pytest.mark.parametrize("text", [SIMPLIFIED_TEXT, TRADITIONAL_TEXT, GB18030_TEXT])
def test_decode_utf8_with_bom(text):
    decoded = decode_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert decoded == text  # BOM 不留在文本里


@pytest.mark.parametrize(
    "data",
    [
        SIMPLIFIED_TEXT.encode("utf-16"),  # 本机字节序 + BOM
        b"\xff\xfe" + SIMPLIFIED_TEXT.encode("utf-16-le"),
        b"\xfe\xff" + SIMPLIFIED_TEXT.encode("utf-16-be"),
    ],
    ids=["native", "le", "be"],
)
def test_decode_utf16_with_bom(data):
    assert decode_bytes(data) == SIMPLIFIED_TEXT


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        (SIMPLIFIED_TEXT, "gb2312"),
        (SIMPLIFIED_TEXT, "gbk"),
        (TRADITIONAL_TEXT, "gbk"),
        (SIMPLIFIED_TEXT, "gb18030"),
        (TRADITIONAL_TEXT, "gb18030"),
        (GB18030_TEXT, "gb18030"),
    ],
)
def test_decode_gb_family(text, encoding):
    data = text.encode(encoding)
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")  # 确认测试数据确实不是合法的 UTF-8，走的是回退分支
    assert decode_bytes(data) == text


def test_gb18030_sample_is_not_gbk():
    with pytest.raises(UnicodeEncodeError):
        GB18030_TEXT.encode("gbk")


# ---------------------------------------------------------------------------
# split_games：标签、多局、空行
# ---------------------------------------------------------------------------


def test_split_single_game_with_tags():
    text = (
        '[Game "Chinese Chess"]\n'
        '[Event "1998年全国象棋个人赛"]\n'
        '[Round "第 1 轮"]\n'
        '[Date "1998.12.13"]\n'
        '[Red "黑龙江 郭莉萍"]\n'
        '[Black "上海 单霞丽"]\n'
        '[Result "1-0"]\n'
        '[Opening "中炮对屏风马"]\n'
        '[Format "ICCS"]\n'
        "\n"
        "1. H2-E2 H9-G7\n"
        "2. H0-G2 I9-H9\n"
        "1-0\n"
    )
    (game,) = split_games(text)
    assert game.headers == {
        "Game": "Chinese Chess",
        "Event": "1998年全国象棋个人赛",
        "Round": "第 1 轮",
        "Date": "1998.12.13",
        "Red": "黑龙江 郭莉萍",
        "Black": "上海 单霞丽",
        "Result": "1-0",
        "Opening": "中炮对屏风马",
        "Format": "ICCS",
    }
    assert game.moves == ["H2-E2", "H9-G7", "H0-G2", "I9-H9"]
    assert game.result == "1-0"
    assert game.index == 1


def test_split_tag_values_kept_exactly():
    text = (
        '[Event "第十届 “五羊杯” 全国象棋冠军邀请赛"]\n'
        '[Site "广州  体育馆"]\n'
        '[RedTeam "黑龙江"]\n'
        f'[FEN "{BLACK_START}"]\n'
        "\n"
        "1... 马8进7 2. 炮二平五 *\n"
    )
    (game,) = split_games(text)
    assert game.headers == {
        "Event": "第十届 “五羊杯” 全国象棋冠军邀请赛",
        "Site": "广州  体育馆",
        "RedTeam": "黑龙江",
        "FEN": BLACK_START,
    }
    assert game.moves == ["马8进7", "炮二平五"]


def test_split_empty_tag_value():
    (game,) = split_games('[Event "甲"]\n[Annotator ""]\n\n1. h2e2 *\n')
    assert game.headers == {"Event": "甲", "Annotator": ""}


MULTI_GAME_TEXT = (
    '[Event "甲"]\r\n'
    '[Red "张 三"]\r\n'
    '[Black "李四"]\r\n'
    "\r\n"
    "1. H2-E2 H9-G7 2. H0-G2 I9-H9 1-0\r\n"
    "\r\n"
    "\r\n"
    '[Event "乙"]\r\n'
    '[Result "0-1"]\r\n'
    "\r\n"
    "1. 炮二平五 马8进7\r\n"
    "2. 马二进三 车9平8 0-1\r\n"
    "\r\n"
    '[Event "丙"]\r\n'
    "\r\n"
    "1. C2.5 H8+7 2. H2+3 R9.8 1/2-1/2\r\n"
)


@pytest.mark.parametrize("newline", ["\r\n", "\n"], ids=["crlf", "lf"])
def test_split_multiple_games(newline):
    games = split_games(MULTI_GAME_TEXT.replace("\r\n", newline))
    assert len(games) == 3
    assert [g.index for g in games] == [1, 2, 3]
    assert [g.headers for g in games] == [
        {"Event": "甲", "Red": "张 三", "Black": "李四"},  # 标签值里不能留下 \r
        {"Event": "乙", "Result": "0-1"},
        {"Event": "丙"},
    ]
    assert [g.moves for g in games] == [
        ["H2-E2", "H9-G7", "H0-G2", "I9-H9"],
        CN4,
        ["C2.5", "H8+7", "H2+3", "R9.8"],
    ]
    assert [g.result for g in games] == ["1-0", "0-1", "1/2-1/2"]


def test_split_tag_line_right_after_movetext_starts_new_game():
    games = split_games('[Event "甲"]\n1. h2e2 h9g7 *\n[Event "乙"]\n1. b0c2 *\n')
    assert [(g.index, g.headers, g.moves) for g in games] == [
        (1, {"Event": "甲"}, ["h2e2", "h9g7"]),
        (2, {"Event": "乙"}, ["b0c2"]),
    ]


def test_split_blank_lines_inside_movetext_do_not_split():
    (game,) = split_games('[Event "甲"]\n\n1. h2e2 h9g7\n\n\n2. h0g2 i9h9\n\n1-0\n\n')
    assert game.moves == ["h2e2", "h9g7", "h0g2", "i9h9"]
    assert game.result == "1-0"


def test_split_text_without_tags_is_one_game():
    (game,) = split_games("\n\n1. 炮二平五 马8进7\n\n2. 马二进三 车9平8\n\n")
    assert game.headers == {}
    assert game.moves == CN4
    assert game.result is None
    assert game.index == 1


@pytest.mark.parametrize("movetext", ["", "*"])
def test_split_game_without_moves(movetext):
    (game,) = split_games(f'[Event "残局"]\n[FEN "{ENDGAME_FEN}"]\n\n{movetext}\n')
    assert game.headers == {"Event": "残局", "FEN": ENDGAME_FEN}
    assert game.moves == []
    assert game.result == (movetext or None)


def test_split_index_numbering():
    text = "\n\n".join(f'[Event "第 {i} 局"]\n\n1. h2e2 h9g7 *' for i in range(1, 6))
    games = split_games(text)
    assert [g.index for g in games] == [1, 2, 3, 4, 5]
    assert [g.headers["Event"] for g in games] == [f"第 {i} 局" for i in range(1, 6)]


# ---------------------------------------------------------------------------
# split_games：回合号、着法写法
# ---------------------------------------------------------------------------

ICCS_HYPHEN4 = ["H2-E2", "H9-G7", "H0-G2", "I9-H9"]
WXF4 = ["C2.5", "H8+7", "H2+3", "R9.8"]
TRADITIONAL4 = ["炮二平五", "馬８進７", "傌二進三", "車９平８"]


@pytest.mark.parametrize(
    ("movetext", "moves"),
    [
        ("1. 炮二平五 马8进7 2. 马二进三 车9平8", CN4),
        ("1.炮二平五 马8进7 2.马二进三 车9平8", CN4),
        ("1.炮二平五马8进7 2.马二进三车9平8", CN4),  # 中文着法之间没有空格
        ("1. 炮二平五\n1... 马8进7\n2. 马二进三\n2... 车9平8", CN4),
        ("1.炮二平五 1...马8进7 2.马二进三 2...车9平8", CN4),
        ("1.\t炮二平五\t马8进7   2.  马二进三  车9平8", CN4),
        ("1. 炮二平五 馬８進７ 2. 傌二進三 車９平８", TRADITIONAL4),
        ("1.炮二平五馬８進７ 2.傌二進三車９平８", TRADITIONAL4),
        ("1. H2-E2 H9-G7 2. H0-G2 I9-H9", ICCS_HYPHEN4),
        ("1.H2-E2 H9-G7 2.H0-G2 I9-H9", ICCS_HYPHEN4),
        ("1. H2E2 H9G7 2. H0G2 I9H9", ["H2E2", "H9G7", "H0G2", "I9H9"]),
        ("1.h2e2 h9g7 2.h0g2 i9h9", ICCS4),
        ("1. h2e2 1... h9g7 2. h0g2 2... i9h9", ICCS4),
        # WXF 着法里的「.」不能被当成回合号
        ("1. C2.5 H8+7 2. H2+3 R9.8", WXF4),
        ("1.C2.5 H8+7 2.H2+3 R9.8", WXF4),
        ("1. c2=5 h8+7 2. h2+3 r9=8", ["c2=5", "h8+7", "h2+3", "r9=8"]),
    ],
)
def test_split_move_numbers_and_spacing(movetext, moves):
    (game,) = split_games(movetext)
    assert game.moves == moves


def test_split_two_digit_move_numbers():
    text = (
        "1. 炮二平五 马8进7\n"
        "2. 马二进三 车9平8\n"
        "3. 车一平二 卒7进1\n"
        "4. 车二进六 马2进3\n"
        "5. 兵七进一 炮8平9\n"
        "6. 车二平三 炮9退1\n"
        "7. 马八进七 士4进5\n"
        "8. 马七进六 炮9平7\n"
        "9. 车三平四 马7进8\n"
        "10.炮八平七 车1平2\n"
        "11. 车九平八 炮2进4\n"
        "12. 兵五进一 {挺中兵}\n"
        "12... 象3进5\n"
    )
    (game,) = split_games(text)
    assert game.moves == OPENING_CN


@pytest.mark.parametrize(
    ("movetext", "moves"),
    [
        ("1.前车进二前马进4 2.後車平二马5退3", ["前车进二", "前马进4", "後車平二", "马5退3"]),
        (
            "1.中兵平六将4平5 2.前兵进一将5平4 3.后兵进一",
            ["中兵平六", "将4平5", "前兵进一", "将5平4", "后兵进一"],
        ),
        ("1.前五进一将4平5 2.后七平八", ["前五进一", "将4平5", "后七平八"]),
    ],
)
def test_split_chinese_tandem_moves_without_spaces(movetext, moves):
    (game,) = split_games(movetext)
    assert game.moves == moves


def test_split_keeps_wxf_tokens_as_written():
    (game,) = split_games("1. C+.5 c-.5 2. +C.5 -c=5 3. R++2 H--3 4. C2=5 c8.5 *")
    assert game.moves == ["C+.5", "c-.5", "+C.5", "-c=5", "R++2", "H--3", "C2=5", "c8.5"]
    assert game.result == "*"


# ---------------------------------------------------------------------------
# split_games：注释、变化、NAG、结果标记
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "movetext",
    [
        "1. 炮二平五 {中炮} 马8进7 2. 马二进三 车9平8",
        "{开局：中炮对屏风马} 1. 炮二平五 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 {也可以走 1. 马二进三 或 h0g2、C2.5} 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 {第一行\n第二行 2. 马八进七 马2进3\n第三行} 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 {第一行\r\n第二行 h0g2\r\n} 马8进7 2. 马二进三 车9平8",
        "1.炮二平五{中炮}马8进7 2.马二进三{跳马}车9平8",
        "1. 炮二平五 马8进7 ; 屏风马 2. 马八进七\n2. 马二进三 车9平8",
        "1. 炮二平五 马8进7 ; 屏风马 2. 马八进七\r\n2. 马二进三 车9平8 ; 出车 3. h0h4\r\n",
        "; 文件开头的注释 1. h2e2\n1. 炮二平五 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 马8进7 ; 1-0\n2. 马二进三 车9平8",
        # 大括号注释里的「;」「(」没有特殊含义，分号注释里的「{」也没有
        "1. 炮二平五 {注意; 这里 2. 马八进七 也行} 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 {参考 (1. 相三进五} 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 马8进7 ; 下面 {不是注释的开头\n2. 马二进三 车9平8",
    ],
)
def test_split_removes_comments(movetext):
    (game,) = split_games(movetext)
    assert game.moves == CN4


@pytest.mark.parametrize(
    "movetext",
    [
        "1. 炮二平五 (1. 相三进五 象7进5) 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 (1. 相三进五 (1. 兵三进一 卒3进1) 象7进5) 马8进7 "
        "2. 马二进三 (2. 马八进七 马2进3 (2... 车9平8 3. 车九平八)) 车9平8",
        "1. 炮二平五 马8进7 (1... 炮8平5\n2. 马二进三 (2. 炮五进四 士4进5)\n马8进7)\n"
        "2. 马二进三 车9平8",
        "1. 炮二平五 (1. 相三进五 {变化里的注释} 象7进5 $2) 马8进7 2. 马二进三 车9平8",
        "1. 炮二平五 (1. 相三进五 {注) 释} 象7进5) 马8进7 2. 马二进三 车9平8",
        "1.炮二平五(1.相三进五象7进5)马8进7 2.马二进三车9平8",
        "1. 炮二平五 马8进7 2. 马二进三 车9平8 (2... 卒7进1 3. 车一平二)",
    ],
)
def test_split_removes_variations(movetext):
    (game,) = split_games(movetext)
    assert game.moves == CN4


@pytest.mark.parametrize(
    ("movetext", "moves"),
    [
        ("1. 炮二平五 $1 马8进7 $14 2. 马二进三 $2 车9平8 $10", CN4),
        ("1. 炮二平五 $1 马8进7 2. 马二进三 车9平8 $146", CN4),
        ("1. h2e2 $1 h9g7 $14 2. h0g2 i9h9", ICCS4),
        ("1. C2.5 $3 H8+7 2. H2+3 $6 R9.8", WXF4),
    ],
)
def test_split_removes_nags(movetext, moves):
    (game,) = split_games(movetext)
    assert game.moves == moves


@pytest.mark.parametrize("marker", ["1-0", "0-1", "1/2-1/2", "*"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_split_result_marker(marker, newline):
    text = newline.join(['[Event "甲"]', "", "1. h2e2 h9g7", f"2. h0g2 i9h9 {marker}", "", ""])
    (game,) = split_games(text)
    assert game.moves == ["h2e2", "h9g7", "h0g2", "i9h9"]
    assert game.result == marker


@pytest.mark.parametrize("marker", ["1-0", "0-1", "1/2-1/2", "*"])
def test_split_result_marker_after_red_move(marker):
    (game,) = split_games(f"1. 炮二平五 马8进7 2. 马二进三\n{marker}")
    assert game.moves == ["炮二平五", "马8进7", "马二进三"]
    assert game.result == marker


def test_split_without_result_marker():
    (game,) = split_games('[Result "1-0"]\n\n1. h2e2 h9g7\n')
    assert game.result is None  # 只看着法末尾的结果标记，不看 [Result] 标签
    assert game.headers == {"Result": "1-0"}
    assert game.moves == ["h2e2", "h9g7"]


def test_split_result_marker_independent_of_result_tag():
    (game,) = split_games('[Result "0-1"]\n\n1. h2e2 h9g7 1-0\n')
    assert game.result == "1-0"
    assert game.headers == {"Result": "0-1"}


# ---------------------------------------------------------------------------
# resolve_game：ICCS、中文记谱
# ---------------------------------------------------------------------------


def test_resolve_standard_start_and_defaults():
    raw = make_raw(["H2-E2", "h9g7", "H0G2", "I9-H9"], {"Event": "甲", "Red": "张 三"})
    parsed = resolve_game(raw)
    assert isinstance(parsed, ParsedGame)
    assert parsed.initial_fen == START_FEN
    assert parsed.moves == ["h2e2", "h9g7", "h0g2", "i9h9"]
    assert parsed.result == "*"
    assert parsed.headers == {"Event": "甲", "Red": "张 三"}


@pytest.mark.parametrize(
    "moves",
    [["H2-E2", "H9-G7"], ["h2e2", "h9g7"], ["H2E2", "H9G7"]],
    ids=["upper-hyphen", "lower", "upper"],
)
def test_resolve_iccs_spellings(moves):
    assert resolve(moves).moves == ["h2e2", "h9g7"]


def test_resolve_chinese_opening():
    assert resolve(OPENING_CN).moves == OPENING_ICCS


@pytest.mark.parametrize(
    ("moves", "expected"),
    [
        # 繁体：砲 馬 傌 進 帥 將 車，黑方全角数字
        (
            ["砲二平五", "馬８進７", "帥五進一", "將５進１", "傌二進三", "車９平８"],
            ["h2e2", "h9g7", "e0e1", "e9e8", "h0g2", "i9h9"],
        ),
        (["炮二平五", "马８进７", "马二进三", "车９平８"], ICCS4),
        # 红方用阿拉伯数字、黑方用中文数字
        (["炮2平5", "马八进七"], ["h2e2", "h9g7"]),
    ],
)
def test_resolve_chinese_variants(moves, expected):
    assert resolve(moves).moves == expected


@pytest.mark.parametrize(
    ("fen", "moves", "expected"),
    [
        (
            ROOKS_FEN,
            ["前车进二", "前马进4", "後車平二", "马5退3"],
            ["i5i7", "e6d4", "i0h0", "e7c8"],
        ),
        (
            PAWNS3_FEN,
            ["中兵平六", "将4平5", "前兵进一", "将5平4", "后兵进一"],
            ["e6d6", "d9e9", "e7e8", "e9d9", "e5e6"],
        ),
        (PAWNS2X2_FEN, ["前五进一", "将4平5", "后七平八"], ["e6e7", "d9e9", "c5b5"]),
        (CANNONS_FEN, ["前炮平五"], ["h4e4"]),
        (CANNONS_FEN, ["后炮平五"], ["h2e2"]),
        (CANNONS_FEN_BLACK, ["前炮平5"], ["h2e2"]),
        (CANNONS_FEN_BLACK, ["後砲平５"], ["h4e4"]),
    ],
)
def test_resolve_chinese_tandem_pieces(fen, moves, expected):
    parsed = resolve(moves, fen=fen)
    assert parsed.moves == expected
    assert parsed.initial_fen == fen


# ---------------------------------------------------------------------------
# resolve_game：WXF
# ---------------------------------------------------------------------------

WXF_CASES = [
    # 红方，初始局面
    (START_FEN, "C2.5", "h2e2", "炮二平五"),
    (START_FEN, "C2=5", "h2e2", "炮二平五"),
    (START_FEN, "c2.5", "h2e2", "炮二平五"),
    (START_FEN, "C2+4", "h2h6", "炮二进四"),
    (START_FEN, "C8-1", "b2b1", "炮八退一"),
    (START_FEN, "H2+3", "h0g2", "马二进三"),
    (START_FEN, "N2+3", "h0g2", "马二进三"),
    (START_FEN, "h8+7", "b0c2", "马八进七"),
    (START_FEN, "R1+1", "i0i1", "车一进一"),
    (START_FEN, "r9+2", "a0a2", "车九进二"),
    (START_FEN, "E3+5", "g0e2", "相三进五"),
    (START_FEN, "B7+5", "c0e2", "相七进五"),
    (START_FEN, "e7+5", "c0e2", "相七进五"),
    (START_FEN, "A4+5", "f0e1", "仕四进五"),
    (START_FEN, "a6+5", "d0e1", "仕六进五"),
    (START_FEN, "K5+1", "e0e1", "帅五进一"),
    (START_FEN, "P3+1", "g3g4", "兵三进一"),
    (START_FEN, "p7+1", "c3c4", "兵七进一"),
    # 黑方：纵线从黑方自己的右手边数起，与中文记谱相同
    (BLACK_START, "H8+7", "h9g7", "马8进7"),
    (BLACK_START, "h8+7", "h9g7", "马8进7"),
    (BLACK_START, "N2+3", "b9c7", "马2进3"),
    (BLACK_START, "R9+1", "i9i8", "车9进1"),
    (BLACK_START, "C2.5", "b7e7", "炮2平5"),
    (BLACK_START, "C8=5", "h7e7", "炮8平5"),
    (BLACK_START, "C8-1", "h7h8", "炮8退1"),
    (BLACK_START, "E3+5", "c9e7", "象3进5"),
    (BLACK_START, "B7+5", "g9e7", "象7进5"),
    (BLACK_START, "A6+5", "f9e8", "士6进5"),
    (BLACK_START, "K5+1", "e9e8", "将5进1"),
    (BLACK_START, "P7+1", "g6g5", "卒7进1"),
    # 同一纵线两个同种子：前 / 后
    (CANNONS_FEN, "C+.5", "h4e4", "前炮平五"),
    (CANNONS_FEN, "C-.5", "h2e2", "后炮平五"),
    (CANNONS_FEN, "+C.5", "h4e4", "前炮平五"),
    (CANNONS_FEN, "-C.5", "h2e2", "后炮平五"),
    (CANNONS_FEN, "c+.5", "h4e4", "前炮平五"),
    (CANNONS_FEN, "-c.5", "h2e2", "后炮平五"),
    (CANNONS_FEN_BLACK, "C+.5", "h2e2", "前炮平5"),
    (CANNONS_FEN_BLACK, "C-.5", "h4e4", "后炮平5"),
    (CANNONS_FEN_BLACK, "+C.5", "h2e2", "前炮平5"),
    (CANNONS_FEN_BLACK, "-C.5", "h4e4", "后炮平5"),
    (ROOKS_FEN, "R++2", "i5i7", "前车进二"),
    (ROOKS_FEN, "+R+2", "i5i7", "前车进二"),
    (ROOKS_FEN, "R-.2", "i0h0", "后车平二"),
    (ROOKS_FEN, "-R.2", "i0h0", "后车平二"),
    (ROOKS_FEN_BLACK, "H++4", "e6d4", "前马进4"),
    (ROOKS_FEN_BLACK, "+H+4", "e6d4", "前马进4"),
    (ROOKS_FEN_BLACK, "H--3", "e7c8", "后马退3"),
    (ROOKS_FEN_BLACK, "-H-3", "e7c8", "后马退3"),
]


@pytest.mark.parametrize(("fen", "wxf", "iccs", "cn"), WXF_CASES)
def test_resolve_wxf(fen, wxf, iccs, cn):
    assert cn_of(fen, [iccs]) == [cn]  # 核对测试数据：WXF 的纵线编号与中文记谱一致
    assert resolve([wxf], fen=fen).moves == [iccs]


@pytest.mark.parametrize(
    ("moves", "expected"),
    [
        (
            ["C2.5", "C8.5", "H2+3", "H8+7", "R1+1", "R9+1", "R1-1", "R9-1"],
            ["h2e2", "h7e7", "h0g2", "h9g7", "i0i1", "i9i8", "i1i0", "i8i9"],
        ),
        (["h2+3", "h8+7", "h3-2", "h7-8"], ["h0g2", "h9g7", "g2h0", "g7h9"]),
        (["c2=5", "n8+7", "n2+3", "r9=8"], ICCS4),
    ],
)
def test_resolve_wxf_game(moves, expected):
    assert resolve(moves).moves == expected


# ---------------------------------------------------------------------------
# resolve_game：[FEN] 起始局面、结果
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moves",
    [
        ["h9g7", "h2e2", "i9h9", "h0g2"],
        ["H9-G7", "H2-E2", "I9-H9", "H0-G2"],
        ["马8进7", "炮二平五", "车9平8", "马二进三"],
        ["H8+7", "C2.5", "R9.8", "H2+3"],
    ],
)
def test_resolve_fen_black_to_move(moves):
    parsed = resolve(moves, fen=BLACK_START)
    assert parsed.initial_fen == BLACK_START
    assert parsed.moves == ["h9g7", "h2e2", "i9h9", "h0g2"]


@pytest.mark.parametrize(
    "moves",
    [["将5平4", "车五平六", "将4平5"], ["K5.4", "R5.6", "K4.5"], ["E9-D9", "E1-D1", "D9-E9"]],
)
def test_resolve_endgame_fen_black_to_move(moves):
    assert cn_of(ENDGAME_FEN, ["e9d9", "e1d1", "d9e9"]) == ["将5平4", "车五平六", "将4平5"]
    parsed = resolve(moves, fen="4k4/9/9/9/9/9/9/9/4R4/5K3 b")  # 没写回合数
    assert parsed.initial_fen == ENDGAME_FEN
    assert parsed.moves == ["e9d9", "e1d1", "d9e9"]


def test_resolve_fen_tag_is_normalised():
    # 有的软件用 E/H 表示相/马、用 r 表示红方走
    tag = "rheakaehr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RHEAKAEHR r"
    parsed = resolve(["h2e2"], fen=tag)
    assert parsed.initial_fen == Position.from_fen(tag).fen() == START_FEN
    assert parsed.moves == ["h2e2"]


def test_resolve_game_without_moves():
    parsed = resolve_game(make_raw([], {"FEN": ENDGAME_FEN}, result="*"))
    assert parsed.initial_fen == ENDGAME_FEN
    assert parsed.moves == []
    assert parsed.result == "*"


@pytest.mark.parametrize(
    ("tag", "marker", "expected"),
    [
        ("0-1", "1-0", "0-1"),  # [Result] 标签优先于着法末尾的结果标记
        ("1-0", "*", "1-0"),
        ("1/2-1/2", "0-1", "1/2-1/2"),
        ("1/2-1/2", None, "1/2-1/2"),
        ("0-1", None, "0-1"),
        (None, "1-0", "1-0"),
        (None, "0-1", "0-1"),
        (None, "1/2-1/2", "1/2-1/2"),
        (None, "*", "*"),
        (None, None, "*"),
    ],
)
def test_resolve_result_precedence(tag, marker, expected):
    headers = {} if tag is None else {"Result": tag}
    parsed = resolve_game(make_raw(["h2e2", "h9g7"], headers, result=marker))
    assert parsed.result == expected


# ---------------------------------------------------------------------------
# resolve_game：错误
# ---------------------------------------------------------------------------


def test_game_format_error_is_value_error():
    assert issubclass(GameFormatError, ValueError)


@pytest.mark.parametrize(
    ("fen", "moves", "ply", "text"),
    [
        (START_FEN, ["h9g7"], 1, "h9g7"),  # 红方先走却走了黑马
        (START_FEN, ["炮二平五", "马8进7", "车一平二"], 3, "车一平二"),  # 车被自己的马挡住
        (START_FEN, ["H2-E2", "H9-G7", "A0-A5"], 3, "A0-A5"),  # 车不能越过兵
        (START_FEN, ["C2.5", "H8+7", "R1.2"], 3, "R1.2"),
        (START_FEN, [*OPENING_CN, "帅五平四"], 25, "帅五平四"),  # 帅不能吃自己的仕
        (BLACK_START, ["h9g7", "h2e2", "i9i5"], 3, "i9i5"),  # 黑先：步数仍从第一步数起
        # 被将军时没有应将（而且将帅照面）
        ("3k5/9/9/9/9/9/9/9/9/4K3R w - - 0 1", ["I0-I9", "D9-E9"], 2, "D9-E9"),
    ],
)
def test_resolve_illegal_move(fen, moves, ply, text):
    with pytest.raises(GameFormatError) as excinfo:
        resolve(moves, fen=fen)
    message = str(excinfo.value)
    assert f"第 {ply} 步" in message
    assert text in message


@pytest.mark.parametrize(
    ("moves", "ply", "text"),
    [
        (["xyz"], 1, "xyz"),
        (["h2e2", "hello"], 2, "hello"),
        (["炮二平五", "马8进7", "炮二平十"], 3, "炮二平十"),
        (["C2.5", "Z8+7"], 2, "Z8+7"),  # 未知的 WXF 棋子字母
        (["h2e2", "h9g7", "j0j1"], 3, "j0j1"),  # 坐标超出棋盘
    ],
)
def test_resolve_unknown_token(moves, ply, text):
    with pytest.raises(GameFormatError) as excinfo:
        resolve(moves)
    message = str(excinfo.value)
    assert f"第 {ply} 步" in message
    assert text in message


@pytest.mark.parametrize(
    "fen",
    [
        "not a fen",
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/RNBAKABNR w",  # 只有 9 行
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNX w",  # 未知字母
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR x",  # 未知走棋方
        "9/9/9/9/9/9/9/9/9/4K4 w",  # 没有黑将
    ],
)
@pytest.mark.parametrize("moves", [[], ["h2e2"]], ids=["no-moves", "one-move"])
def test_resolve_invalid_fen_tag(fen, moves):
    with pytest.raises(GameFormatError):
        resolve(moves, fen=fen)


# ---------------------------------------------------------------------------
# 端到端：字节 → 文本 → 分局 → 着法
# ---------------------------------------------------------------------------

SAMPLE_FILE = """\
[Game "Chinese Chess"]
[Event "1998年全国象棋个人赛"]
[Date "1998.12.13"]
[Red "黑龙江 郭莉萍"]
[Black "上海 单霞丽"]
[Result "1-0"]
[Opening "中炮对屏风马"]

1. 炮二平五 马8进7 2. 马二进三 车9平8
3. 车一平二 {常见} 卒7进1 4. 车二进六 $1 马2进3
5. 兵七进一 (5. 马八进七 象3进5) 炮8平9 1-0

[Event "残局练习"]
[FEN "4k4/9/9/9/9/9/9/9/4R4/5K3 b"]
[Format "WXF"]

1... K5.4 2. R5.6 K4.5 *

[Event "ICCS 测试"]
[Format "ICCS"]

1. H2-E2 H9-G7 ; 中炮对屏风马
2. H0-G2 I9-H9 1/2-1/2
"""


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gbk", "gb18030"])
def test_decode_split_resolve_file(encoding, newline):
    data = SAMPLE_FILE.replace("\n", newline).encode(encoding)
    games = split_games(decode_bytes(data))
    assert [g.index for g in games] == [1, 2, 3]
    assert games[0].headers["Red"] == "黑龙江 郭莉萍"
    assert games[0].headers["Game"] == "Chinese Chess"  # 第一行标签不能被 BOM 弄坏
    assert games[1].moves == ["K5.4", "R5.6", "K4.5"]
    parsed = [resolve_game(g) for g in games]
    assert [(p.initial_fen, p.moves, p.result) for p in parsed] == [
        (START_FEN, OPENING_ICCS[:10], "1-0"),
        (ENDGAME_FEN, ["e9d9", "e1d1", "d9e9"], "*"),
        (START_FEN, ICCS4, "1/2-1/2"),
    ]


# ---------------------------------------------------------------------------
# 随机对局：用规则引擎生成，写成三种记法，再解析回来
# ---------------------------------------------------------------------------

_WXF_PIECES = {
    **dict.fromkeys("帅将", "K"),
    **dict.fromkeys("仕士", "A"),
    **dict.fromkeys("相象", "E"),
    "马": "H",
    "车": "R",
    "炮": "C",
    **dict.fromkeys("兵卒", "P"),
}
_WXF_OPS = {"进": "+", "退": "-", "平": "."}
_DIGITS = {
    **{c: str(i) for i, c in enumerate("零一二三四五六七八九")},
    **{str(i): str(i) for i in range(10)},
}
_TRADITIONAL = str.maketrans("车马炮帅将进后", "車馬砲帥將進後")
_FULL_WIDTH = str.maketrans("0123456789", "０１２３４５６７８９")
# 随机插在着法之间的注释、NAG、变化
_EXTRAS = [
    "{注释：也可以走 1. h2e2 炮二平五 C2.5}",
    "{多行\n注释}",
    "$1",
    "$14",
    "(1. h0g2 (1... h9g7) 马8进7 {变化})",
    "; 行末注释 2. b0c2\n",
]


def to_wxf(board: list[int], move) -> str | None:
    """标准中文记谱转 WXF。WXF 没有约定写法的（三个以上同线兵、多路兵）返回 None。"""
    cn = move_to_chinese(board, move)
    head, tail = cn[:2], _WXF_OPS[cn[2]] + _DIGITS[cn[3]]
    if head[0] in _WXF_PIECES and head[1] in _DIGITS:
        return _WXF_PIECES[head[0]] + _DIGITS[head[1]] + tail
    if head[0] in "前后" and head[1] in _WXF_PIECES:
        piece = board[move[0]]
        if sum(1 for s in range(file_of(move[0]), 90, 9) if board[s] == piece) != 2:
            return None
        return _WXF_PIECES[head[1]] + ("+" if head[0] == "前" else "-") + tail
    return None


def write_move(rng: random.Random, notation: str, pos: Position, move) -> str | None:
    """按记法随机选一种写法。"""
    if notation == "iccs":
        text = move_to_iccs(move)
        return rng.choice([text, text.upper(), f"{text[:2]}-{text[2:]}".upper()])
    if notation == "chinese":
        text = move_to_chinese(pos.board, move)
        if rng.random() < 0.3:
            text = text.translate(_TRADITIONAL)
        if rng.random() < 0.3:
            text = text.translate(_FULL_WIDTH)
        return text
    text = to_wxf(pos.board, move)
    if text is None:
        return None
    if rng.random() < 0.3:
        text = text.translate(str.maketrans("EH", "BN"))
    if rng.random() < 0.3:
        text = text.replace(".", "=")
    if text[1] in "+-" and rng.random() < 0.5:
        text = text[1] + text[0] + text[2:]  # C+.5 → +C.5
    return text.lower() if rng.random() < 0.3 else text


def random_game(rng: random.Random, notation: str, index: int):
    """随机走一盘棋。返回 (PGN 文本, 着法原文, ICCS 着法, 结果)。"""
    pos = Position.start()
    tokens: list[str] = []
    iccs: list[str] = []
    for _ in range(rng.randint(1, 60)):
        legal = pos.legal_moves()
        if not legal:
            break
        move = rng.choice(legal)
        token = write_move(rng, notation, pos, move)
        if token is None:
            break
        tokens.append(token)
        iccs.append(move_to_iccs(move))
        pos.push(move)

    parts = []
    after_move = False  # 上一段是否就是一步着法（中文着法可以直接接在后面）
    for i, token in enumerate(tokens):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.{rng.choice(['', ' '])}{token}")
        elif notation == "chinese" and after_move and rng.random() < 0.5:
            parts[-1] += token  # 中文着法之间不留空格
        else:
            parts.append(token)
        after_move = rng.random() >= 0.25
        if not after_move:
            parts.append(rng.choice(_EXTRAS))
    result = rng.choice(["1-0", "0-1", "1/2-1/2", "*"])
    tags = [f'[Event "随机对局 {index}"]']
    if rng.random() < 0.5:
        tags.append(f'[Result "{result}"]')
    movetext = " ".join(parts).replace("\n ", "\n")
    return "\n".join(tags) + "\n\n" + movetext + f" {result}\n", tokens, iccs, result


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_random_games_roundtrip(newline):
    rng = random.Random(20261004)
    notations = ["iccs", "chinese", "wxf"] * 4
    games = [random_game(rng, notation, i + 1) for i, notation in enumerate(notations)]
    text = "\n".join(g[0] for g in games).replace("\n", newline)

    raws = split_games(text)
    assert len(raws) == len(games)
    for i, (raw, (_, tokens, iccs, result)) in enumerate(zip(raws, games, strict=True)):
        assert raw.index == i + 1
        assert raw.headers["Event"] == f"随机对局 {i + 1}"
        assert raw.moves == tokens, notations[i]
        assert raw.result == result
        parsed = resolve_game(raw)
        assert parsed.initial_fen == START_FEN
        assert parsed.moves == iccs, notations[i]
        assert parsed.result == result


# ---------------------------------------------------------------------------
# 评审发现的问题（回归测试）
# ---------------------------------------------------------------------------


def test_brace_inside_line_comment_does_not_swallow_later_games():
    text = (
        '[Event "一"]\n1. h2e2 h9g7 ; 红方可以考虑{炮八平五\n*\n\n'
        '[Event "二"]\n1. h2e2 *\n\n[Event "三"]\n1. b2e2 *\n'
    )
    games = split_games(text)
    assert [g.headers.get("Event") for g in games] == ["一", "二", "三"]
    assert [g.moves for g in games] == [["h2e2", "h9g7"], ["h2e2"], ["b2e2"]]


def test_header_only_game_is_not_merged_into_next():
    text = (
        '[Event "残局"]\n[Red "甲"]\n[FEN "4k4/9/9/9/9/9/9/9/9/3AK4 w - - 0 1"]\n\n'
        '[Event "对局"]\n[Black "乙"]\n1. h2e2 *\n'
    )
    first, second = split_games(text)
    assert first.moves == [] and first.headers["Red"] == "甲"
    assert second.headers == {"Event": "对局", "Black": "乙"}
    assert resolve_game(second).moves == ["h2e2"]


@pytest.mark.parametrize(
    ("word", "result"),
    [("和局", "1/2-1/2"), ("平局", "1/2-1/2"), ("红方负", "0-1"), ("黑方负", "1-0"),
     ("黑先负", "1-0"), ("红负", "0-1"), ("黑方胜", "0-1")],
)  # fmt: skip
def test_chinese_result_words_in_movetext_and_tag(word, result):
    (game,) = split_games(f"1. 炮二平五 马8进7 {word}\n")
    assert resolve_game(game).result == result
    (game,) = split_games(f'[Result "{word}"]\n1. 炮二平五 马8进7\n')
    assert resolve_game(game).result == result


def test_unescaped_quote_in_tag_value():
    (game,) = split_games('[Event "第一届"棋王"赛"]\n[Red "甲"]\n1. h2e2 *\n')
    assert game.headers == {"Event": '第一届"棋王"赛', "Red": "甲"}


def test_bom_in_the_middle_of_concatenated_files():
    one = '[Event "一"]\n1. h2e2 *\n'.encode("utf-8-sig")
    two = '[Event "二"]\n1. b2e2 *\n'.encode("utf-8-sig")
    games = split_games(decode_bytes(one + b"\n" + two))
    assert [g.headers.get("Event") for g in games] == ["一", "二"]


def test_second_result_marker_is_not_a_new_game():
    games = split_games("1. h2e2 h9g7 1-0 红胜\n")
    assert len(games) == 1 and games[0].result == "1-0"


def test_unknown_result_tag_does_not_override_movetext_result():
    (game,) = split_games('[Result "*"]\n1. h2e2 h9g7 0-1\n')
    assert resolve_game(game).result == "0-1"
    (game,) = split_games('[Result "1-0"]\n1. h2e2 h9g7 0-1\n')
    assert resolve_game(game).result == "1-0"  # 明确的标签仍然优先


def test_fen_tag_with_unicode_digit_is_a_format_error():
    (game,) = split_games(
        '[FEN "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABN① w"]\n1. h2e2\n'
    )
    with pytest.raises(GameFormatError, match="FEN"):
        resolve_game(game)
