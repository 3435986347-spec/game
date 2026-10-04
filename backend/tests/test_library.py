import pytest

from xiangqi.core import START_FEN, Position, parse_iccs
from xiangqi.library import ImportReport, Library, split_games
from xiangqi.library.openings import classify

GAMES = """[Event "测试赛"]
[Date "2020-01-02"]
[Red "张三"]
[Black "李四"]
[Result "1-0"]
1. H2-E2 H9-G7 2. H0-G2 I9-H9 3. I0-H0 1-0

[Event "测试赛"]
[Date "2021-05-06"]
[Red "李四"]
[Black "王五"]
[Result "0-1"]
1. 炮二平五 马8进7 2. 马二进三 马2进3 0-1

[Event "公开赛"]
[Red "王五"]
[Black "张三"]
[Result "1/2-1/2"]
1. C3-C4 C9-E7 1/2-1/2

[Red "坏棋谱"]
1. H2-E2 H9-G7 2. H2-H9 *
"""


def after(*moves: str) -> str:
    pos = Position.start()
    for m in moves:
        pos.push(parse_iccs(m))
    return pos.fen()


@pytest.fixture
def lib():
    library = Library(":memory:")
    library.import_games(split_games(GAMES), source="test.pgn")
    yield library
    library.close()


def test_import_report():
    library = Library(":memory:")
    report = library.import_games(split_games(GAMES), source="test.pgn")
    assert (report.games_seen, report.imported, report.duplicates, report.failed) == (4, 3, 0, 1)
    assert report.errors == [
        {"index": 4, "title": "坏棋谱 vs 黑方", "reason": "第 3 步 H2-H9 不合法：起点没有棋子"}
    ]
    again = library.import_games(split_games(GAMES))
    assert (again.imported, again.duplicates, again.failed) == (0, 3, 1)


def test_same_moves_by_different_players_are_not_duplicates():
    text = """[Red "甲"]\n[Black "乙"]\n1. 炮二平五 马8进7 1/2-1/2\n
[Red "丙"]\n[Black "丁"]\n1. 炮二平五 马8进7 1/2-1/2\n
[Red "甲"]\n[Black "乙"]\n1. H2-E2 H9-G7 1/2-1/2\n"""  # 第 3 局和第 1 局是同一局
    library = Library(":memory:")
    report = library.import_games(split_games(text))
    assert (report.imported, report.duplicates) == (2, 1)


def test_stats_and_openings(lib):
    assert lib.stats() == {"games": 3, "my_games": 0, "indexed_plies": 40}
    assert {row["name"]: row["games"] for row in lib.openings()} == {
        "中炮": 1,
        "中炮对屏风马": 1,
        "仙人指路": 1,
    }


@pytest.mark.parametrize(
    ("kwargs", "reds"),
    [
        ({}, ["李四", "张三", "王五"]),  # 按日期倒序，没有日期的排最后
        ({"q": "张三"}, ["张三", "王五"]),  # 红方或黑方
        ({"event": "测试"}, ["李四", "张三"]),
        ({"result": "1/2-1/2"}, ["王五"]),
        ({"opening": "中炮对屏风马"}, ["李四"]),
        ({"kind": "my_game"}, []),
        ({"fen": after("h2e2", "h9g7")}, ["李四", "张三"]),  # 经过这个局面的对局
    ],
)
def test_search(lib, kwargs, reds):
    total, items = lib.search(**kwargs)
    assert total == len(reds) and [item["red"] for item in items] == reds


def test_search_pagination(lib):
    total, items = lib.search(page=2, page_size=2)
    assert total == 3 and [item["red"] for item in items] == ["王五"]


def test_get_record(lib):
    _, items = lib.search(q="李四", event="测试", result="0-1")
    record = lib.get(items[0]["id"])
    assert [m["cn"] for m in record["moves"]] == ["炮二平五", "马8进7", "马二进三", "马2进3"]
    assert record["fens"][0] == START_FEN and record["fens"][1] == after("h2e2")
    assert len(record["fens"]) == len(record["checks"]) == 5
    assert record["event"] == "测试赛" and record["date"] == "2021-05-06"
    assert lib.get(9999) is None


def test_explorer_counts(lib):
    root = lib.explorer(START_FEN)
    assert root["games"] == 3
    assert [
        (m["cn"], m["games"], m["red_wins"], m["draws"], m["black_wins"]) for m in root["moves"]
    ] == [
        ("炮二平五", 2, 1, 0, 1),
        ("兵七进一", 1, 0, 1, 0),
    ]
    assert lib.explorer(after("c3c4", "c9e7"))["moves"] == []  # 终局：没有下一步
    assert lib.explorer(after("a3a4"))["games"] == 0


def test_index_only_first_plies():
    library = Library(":memory:", index_plies=2)
    library.import_games(split_games(GAMES))
    assert library.explorer(after("h2e2", "h9g7"))["games"] == 2  # 第 2 步之后的局面还在索引里
    assert library.explorer(after("h2e2", "h9g7", "h0g2"))["games"] == 0  # 第 3 步之后不在


def test_delete(lib):
    _, items = lib.search(q="王五", result="1/2-1/2")
    assert lib.delete(items[0]["id"]) is True
    assert lib.stats()["games"] == 2 and lib.explorer(START_FEN)["games"] == 2
    assert lib.delete(items[0]["id"]) is False


def test_save_game_updates_same_record(lib):
    headers = {"Event": "人机对战", "Red": "我", "Black": "AI"}
    first = lib.save_game(initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers)
    again = lib.save_game(
        initial_fen=START_FEN,
        moves=["h2e2", "h9g7"],
        result="1-0",
        headers=headers,
        library_id=first,
    )
    assert again == first
    record = lib.get(first)
    assert record["kind"] == "my_game" and record["result"] == "1-0" and len(record["moves"]) == 2
    assert lib.stats()["my_games"] == 1
    # 相同着法的两局自己的对局不会被当作重复
    other = lib.save_game(initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers)
    assert other != first and lib.stats()["my_games"] == 2


def test_save_game_keeps_id_when_not_newest(lib):
    headers = {"Red": "我", "Black": "AI"}
    first = lib.save_game(initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers)
    lib.save_game(initial_fen=START_FEN, moves=["c3c4"], result="*", headers=headers)
    again = lib.save_game(
        initial_fen=START_FEN, moves=["h2e2", "h7e7"], result="0-1", headers=headers,
        library_id=first,
    )  # fmt: skip
    assert again == first and lib.get(first)["opening"] == "顺炮"
    replies = {m["cn"]: m["games"] for m in lib.explorer(after("h2e2"))["moves"]}
    assert replies == {"马8进7": 2, "炮8平5": 1}  # 索引已换成新着法


def test_save_game_after_record_deleted_or_wrong_kind(lib):
    headers = {"Red": "我"}
    first = lib.save_game(initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers)
    lib.delete(first)
    other = lib.save_game(initial_fen=START_FEN, moves=["b0c2"], result="*", headers=headers)
    assert other != first  # id 不复用，另一局不会被当成已删除的那局
    again = lib.save_game(
        initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers, library_id=first
    )
    assert again not in (first, other) and lib.get(other)["moves"][0]["iccs"] == "b0c2"
    assert lib.stats()["my_games"] == 2
    # 不会覆盖导入的棋谱，也不会删掉它的局面索引
    _, items = lib.search(q="王五", result="1/2-1/2")
    imported = items[0]["id"]
    other = lib.save_game(
        initial_fen=START_FEN, moves=["h2e2"], result="*", headers=headers, library_id=imported
    )
    assert other != imported and lib.get(imported)["kind"] == "library"
    assert lib.explorer(after("c3c4"))["games"] == 1


def test_import_from_file_on_disk(tmp_path):
    db = tmp_path / "lib.db"
    library = Library(db)
    library.import_games(split_games(GAMES))
    library.close()
    reopened = Library(db)
    assert reopened.stats()["games"] == 3
    reopened.close()


@pytest.mark.parametrize(
    ("moves", "name"),
    [
        (["h2e2", "h9g7", "h0g2", "b9c7"], "中炮对屏风马"),
        (["b2e2", "b9c7", "b0c2", "h9g7"], "中炮对屏风马"),
        (["h2e2", "b9c7", "h0g2", "h7f7", "i0h0", "h9g7"], "中炮对反宫马"),
        (["h2e2", "h7e7"], "顺炮"),
        (["h2e2", "b7e7"], "列炮"),
        (["b2e2", "b7e7"], "顺炮"),
        (["h2e2", "g6g5"], "中炮"),
        (["c3c4"], "仙人指路"),
        (["c0e2"], "飞相局"),
        (["b0c2"], "起马局"),
        (["h2d2"], "过宫炮"),
        (["b2d2"], "仕角炮"),
        (["a3a4"], "其他开局"),
    ],
)
def test_classify_openings(moves, name):
    assert classify(START_FEN, moves) == name


def test_classify_needs_standard_start():
    assert classify("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1", ["e0e1"]) is None
    assert classify(START_FEN, []) is None


def many_games(n: int, event: str = "批量") -> str:
    return "".join(f'[Event "{event}{i}"]\n1. H2-E2 H9-G7 *\n\n' for i in range(n))


def test_reimport_does_not_hold_write_lock(tmp_path):
    import sqlite3

    db = tmp_path / "lib.db"
    library = Library(db)
    library.import_games(split_games(many_games(400)))
    probes = []

    def probe(_report):  # 重复导入进行中，另一个连接要能写
        other = sqlite3.connect(db, timeout=0.2)
        try:
            other.execute("INSERT INTO meta (key, value) VALUES (?, 'x')", (f"probe{len(probes)}",))
            other.commit()
            probes.append("ok")
        except sqlite3.OperationalError as e:
            probes.append(str(e))
        finally:
            other.close()

    report = library.import_games(split_games(many_games(400)), progress=probe)
    assert report.duplicates == 400 and probes and set(probes) == {"ok"}
    library.close()


def test_failed_import_rolls_back_and_releases_lock(tmp_path):
    import sqlite3

    db = tmp_path / "lib.db"
    library = Library(db)

    def games_then_crash():
        yield from split_games(many_games(3))
        raise OSError("磁盘出错")

    report = None
    with pytest.raises(OSError):
        library.import_games(games_then_crash(), report=(report := ImportReport()))
    assert report.imported == 0  # 没提交的不算
    other = sqlite3.connect(db, timeout=0.2)
    other.execute("INSERT INTO meta (key, value) VALUES ('probe', 'x')")  # 写锁已释放
    other.commit()
    other.close()
    assert library.stats()["games"] == 0
    library.import_games(split_games(many_games(1, "另一个")))
    assert library.stats()["games"] == 1  # 之前没提交的对局不会被顺带提交
    library.close()


def test_import_can_be_cancelled():
    import threading

    library = Library(":memory:")
    cancel = threading.Event()

    def stop_after_first(report):
        cancel.set()

    report = library.import_games(
        split_games(many_games(450)), progress=stop_after_first, cancel=cancel
    )
    assert report.games_seen == 200 and library.stats()["games"] == 200  # 已导入的保留


def test_search_treats_like_wildcards_literally(lib):
    assert lib.search(q="_")[0] == 0 and lib.search(q="%")[0] == 0
    assert lib.search(event="测试%")[0] == 0 and lib.search(event="测试")[0] == 2


def test_close_closes_connections_of_all_threads(tmp_path):
    import sqlite3
    import threading

    library = Library(tmp_path / "lib.db")
    conns = []
    worker = threading.Thread(target=lambda: conns.append(library.connect()))
    worker.start()
    worker.join()
    library.close()
    with pytest.raises(sqlite3.ProgrammingError):
        conns[0].execute("SELECT 1")
