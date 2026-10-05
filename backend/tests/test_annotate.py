import time

from test_api_train import client, import_game  # noqa: F401  （复用 fixture）

from xiangqi.core import RED, Position, parse_iccs
from xiangqi.llm import EngineLine, build_context
from xiangqi.training.annotate import (
    build_summary_context,
    side_of_ply,
    threat_fact,
    turning_points,
)


def test_turning_points_prefers_big_swings_and_keeps_brilliant_moves():
    curve = [0.5, 0.52, 0.2, 0.25, 0.7, 0.71, 0.72]
    grades = {1: "好棋", 2: "漏着", 3: "好棋", 4: "漏着", 5: "妙着", 6: "好棋"}
    assert turning_points(curve, grades) == [2, 4, 5]
    flat = [0.5] * 20
    assert turning_points(flat, {}) == []


def test_threat_fact_describes_capture_and_mate():
    # 黑车在 a5，红马 c4 走到 b6 之后（假设），空着法：红方最想吃车
    after = Position.from_fen("3k5/9/9/9/r8/9/1N7/9/9/4K4 b - - 0 1")
    text, move = threat_fact(after, RED, EngineLine("b3a5", ("b3a5",), 0.9))
    assert text == "如果黑方停一步不走，红方最想走马八进九，吃掉黑方的车(a5)"
    mate_pos = Position.from_fen("4k4/R8/9/9/9/9/9/9/1R7/3K5 b - - 0 1")
    text, _ = threat_fact(mate_pos, RED, EngineLine("b1b9", ("b1b9",), 1.0, 1))
    assert text.endswith("1步之内就能将死对方")


def test_side_of_ply():
    fen = Position.start().fen()
    assert side_of_ply(fen, 1) == "red" and side_of_ply(fen, 2) == "black"


def test_cache_ids_follow_content():
    pos = Position.start()
    kwargs = dict(before=[], after=[], win_before=None, win_after=None, grade="漏着", level="入门")
    plain = build_context(pos, parse_iccs("h2e2"), **kwargs)
    guess = build_context(pos, parse_iccs("h2e2"), **kwargs, extra_facts=["大师走的是马二进三"])
    assert plain.cache_id() != guess.cache_id()  # 猜着时的讲解不和普通讲解共用缓存

    record = {"initial_fen": pos.fen(), "moves": ["h2e2", "h9g7"], "result": "1-0"}
    side = {"accuracy": 90, "phases": {"开局": 90}}
    stats = {"red": side, "black": side}
    point = {"ply": 1, "side": "red", "cn": "炮二平五", "grade": "好棋", "phase": "开局",
             "red_before": 0.5, "red_after": 0.6, "intent": "抢占中路"}  # fmt: skip
    first = build_summary_context(record, stats, [point], level="入门")
    again = build_summary_context(record, stats, [point], level="入门")
    changed = build_summary_context(record, stats, [{**point, "red_after": 0.8}], level="入门")
    assert first.cache_id() == again.cache_id() != changed.cache_id()  # 重新复盘后总结要重做


def wait(c, path: str) -> dict:
    for _ in range(200):
        view = c.get(path).json()
        if view["status"] in ("done", "error", "none"):
            return view
        time.sleep(0.05)
    raise AssertionError(f"{path} 没有完成")


def test_annotate_flow(client):  # noqa: F811
    gid = import_game(client)
    assert client.post(f"/api/library/games/{gid}/annotate").status_code == 409  # 先复盘
    client.post(f"/api/library/games/{gid}/review")
    assert wait(client, f"/api/library/games/{gid}/review")["status"] == "done"

    started = client.post(f"/api/library/games/{gid}/annotate").json()
    assert started["status"] == "running"
    view = wait(client, f"/api/library/games/{gid}/annotate")
    assert view["status"] == "done", view
    # 假引擎下每步红方期望得分都在 0.59 / 0.41 之间跳：6 步都算转折点
    assert [m["ply"] for m in view["moves"]] == [1, 2, 3, 4, 5, 6]
    first = view["moves"][0]
    assert first["side"] == "red" and first["cn"] == "炮二平五" and first["phase"] == "开局"
    assert first["intent"]["source"] == "template" and first["intent"]["headline"]
    summary = view["summary"]
    assert summary["source"] == "template" and "红方胜" in summary["overall"]
    assert summary["opening"].startswith("布局是")

    # 已有解读：再次请求直接返回；重新复盘后解读作废
    assert client.post(f"/api/library/games/{gid}/annotate").json()["status"] == "done"
    # 依据的复盘已经不是现在这次（解读期间重新复盘了）：不保存，也不删掉现有的解读
    library = client.app.state.library
    stale = [(0, "summary", {"overall": "过时"})]
    assert not library.save_annotations(gid, stale, reviewed_at="2000-01-01 00:00:00")
    assert client.get(f"/api/library/games/{gid}/annotate").json() == view
    client.post(f"/api/library/games/{gid}/review", json={"force": True})
    assert wait(client, f"/api/library/games/{gid}/review")["status"] == "done"
    assert client.get(f"/api/library/games/{gid}/annotate").json()["status"] == "none"
