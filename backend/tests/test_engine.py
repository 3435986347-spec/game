import asyncio
import random
import sys
from contextlib import aclosing
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE, fake_engine_command

from xiangqi.core import START_FEN, Position, legal_moves, parse_iccs
from xiangqi.engine import (
    AnalysisResult,
    EngineConfig,
    EngineError,
    EngineService,
    EngineUnavailable,
    InfoLine,
    Limit,
    UciEngine,
    choose_move,
    get_level,
)
from xiangqi.engine.uci import from_engine_move, parse_info, to_engine_move

# ---- 解析与坐标转换 ----


def test_parse_pikafish_info_line():
    line = (
        "info depth 18 seldepth 25 multipv 2 score cp -35 wdl 120 700 180 nodes 123456 "
        "nps 900000 hashfull 12 tbhits 0 time 137 pv h0g2 i9h9 i0h0"
    )
    info = parse_info(line, "pikafish")
    assert info == InfoLine(
        multipv=2,
        depth=18,
        pv=["h0g2", "i9h9", "i0h0"],
        score_cp=-35,
        wdl=(120, 700, 180),
        nodes=123456,
    )
    assert info.expected_score() == pytest.approx(0.47)


def test_parse_fairy_coordinates_and_bounds():
    info = parse_info(
        "info depth 6 score cp 12 lowerbound nodes 10 pv h10g8 h1g3", "fairy-stockfish"
    )
    assert info.pv == ["h9g7", "h0g2"] and info.bound == "lower" and info.multipv == 1


def test_parse_mate_and_ignored_lines():
    assert parse_info("info depth 3 score mate -2 pv e0e1", "pikafish").mate == -2
    assert parse_info("info string NNUE evaluation enabled", "pikafish") is None
    assert parse_info("info depth 9 currmove h2e2 currmovenumber 1", "pikafish") is None


@pytest.mark.parametrize(
    ("ours", "fairy"), [("h2e2", "h3e3"), ("a9a7", "a10a8"), ("i0i9", "i1i10")]
)
def test_coordinate_conversion(ours, fairy):
    assert to_engine_move(ours, "fairy-stockfish") == fairy
    assert from_engine_move(fairy, "fairy-stockfish") == ours
    assert to_engine_move(ours, "pikafish") == ours


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (InfoLine(1, 1, ["a0a1"], wdl=(1000, 0, 0)), 1.0),
        (InfoLine(1, 1, ["a0a1"], wdl=(0, 1000, 0)), 0.5),
        (InfoLine(1, 1, ["a0a1"], mate=3), 1.0),
        (InfoLine(1, 1, ["a0a1"], mate=-1), 0.0),
        (InfoLine(1, 1, ["a0a1"], score_cp=0), 0.5),
    ],
)
def test_expected_score(info, expected):
    assert info.expected_score() == pytest.approx(expected)


# ---- 与（假）引擎进程通信 ----


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("flavor", ["pikafish", "fairy-stockfish"])
def test_analyse_with_fake_engine(flavor):
    flags = ("--flavor=fairy",) if flavor == "fairy-stockfish" else ()

    async def scenario():
        engine = UciEngine(fake_engine_command(*flags), flavor=flavor, options={"Threads": 2})
        await engine.start()
        try:
            result = await engine.analyse(START_FEN, ["h2e2"], limit=Limit(nodes=1000), multipv=3)
        finally:
            await engine.close()
        return engine, result

    engine, result = run(scenario())
    assert engine.name == "FakeEngine 1.0" and not engine.alive
    assert [line.multipv for line in result.lines] == [1, 2, 3]
    assert result.lines[0].wdl is not None  # UCI_ShowWDL 自动打开
    pos = Position.start()
    pos.push(parse_iccs("h2e2"))
    legal = set(legal_moves(pos.board, pos.turn))
    assert all(parse_iccs(line.move) in legal for line in result.lines)  # 坐标已转换回本项目格式
    assert result.bestmove == result.lines[0].move


def test_engine_crash_reports_missing_network():
    async def scenario():
        engine = UciEngine(fake_engine_command("--die-on-go"))
        await engine.start()
        with pytest.raises(EngineError, match="pikafish.nnue") as info:
            await engine.analyse(START_FEN, limit=Limit(depth=5))
        return engine, str(info.value)

    engine, message = run(scenario())
    assert not engine.alive
    assert "eval_file" in message  # 附带了怎么解决的提示


def test_infinite_analysis_stops_cleanly():
    async def scenario():
        engine = UciEngine(fake_engine_command())
        await engine.start()
        snapshots = []
        async with aclosing(engine.analyse_infinite(START_FEN, multipv=2)) as gen:
            async for lines in gen:
                snapshots.append(lines)
                if len(snapshots) >= 5:
                    break
        # 停下之后引擎可以继续正常工作
        result = await engine.analyse(START_FEN, limit=Limit(nodes=100))
        await engine.close()
        return snapshots, result

    snapshots, result = run(scenario())
    assert len(snapshots) == 5 and len(snapshots[-1]) == 2
    assert result.bestmove is not None


def test_cancelling_infinite_analysis_task_releases_engine():
    async def scenario():
        engine = UciEngine(fake_engine_command())
        await engine.start()

        async def consume():
            async with aclosing(engine.analyse_infinite(START_FEN)) as gen:
                async for _ in gen:
                    pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await asyncio.wait_for(engine.analyse(START_FEN, limit=Limit(nodes=100)), 5)
        await engine.close()
        return result

    assert run(scenario()).bestmove is not None


def test_missing_binary():
    with pytest.raises(EngineError, match="无法启动引擎"):
        run(UciEngine(["/nonexistent/pikafish"]).start())


# ---- 引擎服务 ----


def test_service_not_configured():
    service = EngineService(None)
    with pytest.raises(EngineUnavailable, match="未配置"):
        run(service.player())
    assert run(service.status())["ok"] is False


def test_service_missing_file(tmp_path):
    service = EngineService(EngineConfig(path=tmp_path / "pikafish"))
    with pytest.raises(EngineUnavailable, match="找不到引擎文件"):
        run(service.player())


def test_service_restarts_after_crash():
    config = EngineConfig(path=Path(sys.executable), args=(str(FAKE_ENGINE),))

    async def scenario():
        service = EngineService(config)
        first = await service.player()
        first._proc.kill()  # 模拟引擎崩溃
        await asyncio.to_thread(first._proc.wait)
        second = await service.player()
        status = await service.status()
        await service.close()
        return first, second, status

    first, second, status = run(scenario())
    assert first is not second and status["ok"] and status["name"] == "FakeEngine 1.0"


# ---- 难度 ----


def _result(*scores: float) -> AnalysisResult:
    lines = [
        InfoLine(i + 1, 10, [f"a0a{i + 1}"], wdl=(round(s * 1000), 0, 1000 - round(s * 1000)))
        for i, s in enumerate(scores)
    ]
    return AnalysisResult(lines=lines, bestmove=lines[0].move if lines else None)


def test_full_strength_always_picks_best():
    result = _result(0.40, 0.55, 0.30)  # 最佳着法不一定排在第一（不同深度）
    assert choose_move(result, get_level(10), random.Random(0)) == "a0a2"


def test_candidate_pool_applies_max_drop():
    from xiangqi.engine.difficulty import candidate_pool

    result = _result(0.60, 0.58, 0.50, 0.10)
    pool = candidate_pool(result, get_level(3))  # max_drop 0.35：0.10 被排除
    assert [move for move, _ in pool] == ["a0a1", "a0a2", "a0a3"]
    assert [move for move, _ in candidate_pool(result, get_level(1))] == [
        "a0a1",
        "a0a2",
        "a0a3",
        "a0a4",
    ]  # 1 级 max_drop 0.60，四个都在


def test_low_level_actually_varies():
    result = _result(0.60, 0.58, 0.50)
    picks = {choose_move(result, get_level(3), random.Random(seed)) for seed in range(300)}
    assert picks == {"a0a1", "a0a2", "a0a3"}


def _mate_result() -> AnalysisResult:
    lines = [
        InfoLine(1, 20, ["a0a9"], mate=2, wdl=(1000, 0, 0)),  # 引擎排序不一定把一步杀放第一
        InfoLine(2, 20, ["a1a9"], mate=1, wdl=(1000, 0, 0)),
        InfoLine(3, 20, ["e0e1"], score_cp=900, wdl=(1000, 0, 0)),  # WDL 饱和，看起来也是必胜
    ]
    return AnalysisResult(lines=lines, bestmove="a1a9")


@pytest.mark.parametrize("level", [5, 6, 7, 8, 9, 10])
def test_strong_levels_never_miss_mate_in_one(level):
    picks = {choose_move(_mate_result(), get_level(level), random.Random(s)) for s in range(200)}
    assert picks == {"a1a9"}


def test_when_being_mated_prefer_not_losing_then_longest_resistance():
    from xiangqi.engine.difficulty import utility

    mated_fast = InfoLine(1, 10, ["a0a1"], mate=-1)
    mated_slow = InfoLine(2, 10, ["a0a2"], mate=-5)
    holding = InfoLine(3, 10, ["a0a3"], score_cp=-300, wdl=(0, 200, 800))
    assert utility(holding) > utility(mated_slow) > utility(mated_fast)


def test_higher_level_prefers_best_more_often():
    result = _result(0.60, 0.55)
    counts = {}
    for lv in (2, 8):
        level = get_level(lv)
        counts[lv] = sum(
            choose_move(result, level, random.Random(seed)) == "a0a1" for seed in range(500)
        )
    assert counts[8] > counts[2]


def test_no_candidates():
    assert (
        choose_move(AnalysisResult(lines=[], bestmove=None), get_level(5), random.Random()) is None
    )


def test_level_bounds():
    with pytest.raises(ValueError):
        get_level(11)


def test_bound_scores_do_not_replace_exact_results():
    from xiangqi.engine.uci import _merge

    lines = {}
    _merge(lines, InfoLine(1, 11, ["h0g2"], score_cp=20))
    _merge(lines, InfoLine(1, 12, ["i0h0"], score_cp=900, bound="lower"))  # 窗口失败的临时分数
    assert lines[1].score_cp == 20
    _merge(lines, InfoLine(1, 12, ["i0h0"], score_cp=35))  # 随后的精确结果
    assert lines[1].score_cp == 35


def test_snapshot_drops_duplicate_moves():
    from xiangqi.engine.uci import _snapshot

    lines = {
        1: InfoLine(1, 12, ["c3c4"], score_cp=30),  # 新一层：c3c4 升到第一
        2: InfoLine(2, 11, ["c3c4"], score_cp=25),  # 上一层的第二名还是 c3c4
        3: InfoLine(3, 11, ["h0g2"], score_cp=20),
    }
    assert [line.move for line in _snapshot(lines)] == ["c3c4", "h0g2"]


# ---- 评审中发现的问题的回归测试 ----


def test_fairy_bound_after_wdl_is_recognised():
    line = (
        "info depth 18 multipv 1 score cp -19 wdl 25 916 59 upperbound nodes 1913040 pv h8h4 a4a5"
    )
    info = parse_info(line, "fairy-stockfish")
    assert info.bound == "upper" and info.wdl == (25, 916, 59) and info.pv == ["h7h3", "a3a4"]


@pytest.mark.parametrize(("move", "flavor"), [("a0a1", "fairy-stockfish"), ("h10g8", "pikafish")])
def test_out_of_board_engine_move_points_to_flavor(move, flavor):
    with pytest.raises(EngineError, match="flavor"):
        from_engine_move(move, flavor)


def test_malformed_engine_move():
    with pytest.raises(EngineError, match="无法识别"):
        from_engine_move("a-1a1", "fairy-stockfish")


def test_relative_engine_path_becomes_absolute(tmp_path, monkeypatch):
    """引擎在自己的目录中启动（cwd），相对路径的可执行文件必须先变成绝对路径。"""
    from xiangqi.engine.uci import engine_command

    monkeypatch.chdir(tmp_path)
    relative = Path("engines") / "pikafish"
    assert engine_command(relative) == [str(tmp_path / "engines" / "pikafish")]
    engine = UciEngine([str(relative)], cwd=tmp_path / "engines")
    assert engine.command[0] == str(tmp_path / "engines" / "pikafish")
    assert UciEngine(["pikafish"], cwd=tmp_path).command[0] == "pikafish"  # PATH 中的命令不变


def test_cancelled_start_leaves_no_process():
    async def scenario():
        engine = UciEngine(fake_engine_command("--slow-start"))
        task = asyncio.create_task(engine.start())
        await asyncio.sleep(0.3)  # 进程已启动，握手还没完成
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return engine

    engine = run(scenario())
    assert engine._proc is None and not engine.alive


def test_find_network_in_parent_directories(tmp_path):
    from xiangqi.engine.uci import find_network

    binary = tmp_path / "Pikafish" / "MacOS" / "pikafish-apple-silicon"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    assert find_network(binary) is None
    (tmp_path / "Pikafish" / "pikafish.nnue").write_text("")
    assert find_network(binary) == tmp_path / "Pikafish" / "pikafish.nnue"
    (binary.parent / "pikafish.nnue").write_text("")  # 同目录的优先
    assert find_network(binary) == binary.parent / "pikafish.nnue"


def test_service_sets_eval_file_found_in_parent(tmp_path):
    binary = tmp_path / "Pikafish" / "Linux" / "pikafish-avx2"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    (tmp_path / "Pikafish" / "pikafish.nnue").write_text("")
    engine = EngineService(EngineConfig(path=binary))._create()
    assert engine.options["EvalFile"] == str(tmp_path / "Pikafish" / "pikafish.nnue")
    assert Path(engine.command[0]).is_absolute()
