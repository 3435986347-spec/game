"""飞刀检查必须保留完整历史，并拒绝无效棋谱、评分和人为送回的防守。"""

import asyncio
import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xiangqi.core import Position, move_to_iccs, parse_iccs
from xiangqi.core.board import BLACK, CANNON, KING, KNIGHT, PAWN, RED, ROOK, sq
from xiangqi.engine import AnalysisResult, InfoLine, Limit
from xiangqi.openings import TRAP_LINES
from xiangqi.openings import check as checker
from xiangqi.openings.check import TrapCheck, check_trap, verify_trap
from xiangqi.openings.index import TrapLine, TrapMove
from xiangqi.openings.traps import Trap

BASE = TRAP_LINES["qima-shisan"]


class HistoryEngine:
    def __init__(self, overrides=None, invalid=None):
        self.overrides = overrides or {}
        self.invalid = invalid
        self.calls = []

    async def analyse(self, fen, moves=(), *, limit, multipv, searchmoves=()):
        position = Position.from_fen(fen)
        for text in moves:
            position.push(parse_iccs(text))
        self.calls.append((fen, tuple(moves), position.fen(), searchmoves, limit))
        move = searchmoves[0] if searchmoves else move_to_iccs(position.legal_moves()[0])
        score = self.overrides.get((position.fen(), tuple(searchmoves)), 0.5)
        w = round(score * 1000)
        info = InfoLine(1, 24, [move], wdl=(w, 0, 1000 - w), nodes=1000)
        result = AnalysisResult([info], move)
        return self.invalid(result) if self.invalid else result


def make_line(initial, moves, trap_ply, refute, side=RED):
    position = initial.copy()
    fens = [position.fen()]
    steps = []
    for index, move in enumerate(moves, 1):
        position.push(parse_iccs(move))
        steps.append(TrapMove(index, move, move, position.fen()))
        fens.append(position.fen())
    position = initial.copy()
    for move in moves[:trap_ply]:
        position.push(parse_iccs(move))
    refute_steps = []
    for index, move in enumerate(refute, trap_ply + 1):
        position.push(parse_iccs(move))
        refute_steps.append(TrapMove(index, move, move, position.fen()))
    trap = Trap("fixture", "测试", side, "", "", trap_ply, tuple(moves), tuple(refute))
    return TrapLine(
        trap, tuple(moves), tuple(fens), tuple(steps), tuple(refute), tuple(refute_steps),
    )


def mirrored_base():
    initial = Position.from_fen(BASE.fens[0])
    board = [0] * 90
    for square, piece in enumerate(initial.board):
        board[sq(square % 9, 9 - square // 9)] = -piece

    def mirror(text):
        frm, to = parse_iccs(text)
        return move_to_iccs((sq(frm % 9, 9 - frm // 9), sq(to % 9, 9 - to // 9)))

    return make_line(
        Position(board, BLACK), tuple(map(mirror, BASE.moves)), BASE.trap.trap_ply,
        tuple(map(mirror, BASE.refute)), BLACK,
    )


@pytest.mark.parametrize("line", [BASE, mirrored_base()])
def test_complete_history_scores_and_defender_checks_for_both_sides(line):
    accepted_fen = line.fens[line.trap.trap_ply + 1]
    engine = HistoryEngine({(accepted_fen, ()): 0.73})
    report = asyncio.run(check_trap(engine, line, Limit(depth=12)))
    assert report.passed and report.main_score == 1.0
    assert report.accepted_score == 0.73
    assert [item.ply for item in report.defense_checks] == [12, 14, 16]
    assert all(item.drop == 0.0 for item in report.defense_checks)
    assert all(call[0] == line.fens[0] for call in engine.calls)
    assert engine.calls[0][1] == line.moves[:10]
    assert engine.calls[-1][1] == line.moves[:11] + line.refute
    assert report.evidence[-1].moves == line.moves[:11] + line.refute
    assert report.evidence[-1].nodes == 1000
    terminal = next(item for item in report.evidence if item.label == "主变终点")
    assert terminal.terminal_reason == "checkmate" and terminal.moves == line.moves
    assert asdict(report)["evidence"][0]["limit"] == {
        "nodes": None, "depth": 12, "movetime_ms": None,
    }


def test_each_defender_move_is_checked_not_only_entry_and_endpoint():
    middle = BASE.refute_steps[1].fen
    engine = HistoryEngine({(middle, ()): 0.7, (middle, (BASE.refute[2],)): 0.5})
    report = asyncio.run(check_trap(engine, BASE, Limit(depth=12)))
    assert not report.passed
    assert report.refute_entry_score == report.refute_score == 0.5
    assert report.defense_checks[1].drop == pytest.approx(0.2)
    assert any("第 14 步" in error for error in report.errors)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({(BASE.start_fen, (BASE.moves[10],)): 0.4}, "低于 0.45"),
        ({(BASE.start_fen, ()): 0.7}, "超过 0.10"),
        ({(BASE.trap_fen, (BASE.refute[0],)): 0.4}, "破解第一步"),
        ({(BASE.refute_steps[-1].fen, ()): 0.4}, "破解变终点"),
    ],
)
def test_score_gates(overrides, message):
    # BASE 的破解终点轮到黑方，其他红方弃子前的评分也恰好是走棋方视角。
    report = asyncio.run(check_trap(HistoryEngine(overrides), BASE, Limit(depth=12)))
    assert not report.passed and any(message in error for error in report.errors)


def test_non_winning_main_line_is_not_accepted():
    line = replace(
        BASE, moves=BASE.moves[:14], fens=BASE.fens[:15], steps=BASE.steps[:14],
    )
    report = asyncio.run(check_trap(HistoryEngine(), line, Limit(depth=12)))
    assert not report.passed and report.main_score == 0.5
    assert any("主变终点" in error for error in report.errors)


def test_rule_evaluation_retains_repetition_history():
    position = Position.start()
    moves = ("i0i1", "i9i8", "i1i0", "i8i9") * 2
    for text in moves:
        position.push(parse_iccs(text))
    evidence = []
    engine = HistoryEngine()
    score = asyncio.run(checker._score(
        engine, position, RED, Limit(depth=12), initial_fen=Position.start().fen(),
        label="重复局面", evidence=evidence,
    ))
    assert score == 0.5 and not engine.calls
    assert evidence[0].terminal_reason == "repetition" and evidence[0].moves == moves


def test_limits_are_inclusive_and_not_rounded_before_comparison():
    overrides = {
        (BASE.start_fen, ()): 0.55,
        (BASE.start_fen, (BASE.moves[10],)): 0.45,
        (BASE.trap_fen, ()): 0.5,
        (BASE.trap_fen, (BASE.refute[0],)): 0.45,
        (BASE.refute_steps[-1].fen, ()): 0.45,
    }
    report = asyncio.run(check_trap(HistoryEngine(overrides), BASE, Limit(depth=12)))
    assert report.passed


def test_illegal_moves_and_stale_fens_are_rejected_before_engine_search():
    cases = [
        replace(BASE, moves=("a0a9", *BASE.moves[1:])),
        replace(BASE, fens=(BASE.fens[0], BASE.fens[0], *BASE.fens[2:])),
        replace(BASE, refute=("bad", *BASE.refute[1:])),
        replace(BASE, refute=(), refute_steps=()),
    ]
    for line in cases:
        engine = HistoryEngine()
        report = asyncio.run(check_trap(engine, line, Limit(depth=12)))
        assert not report.passed and report.failure_kind == "data" and not engine.calls


def test_crossing_pawn_cannot_turn_an_exchange_gain_into_net_sacrifice():
    board = [0] * 90
    for file, rank, piece in [
        (4, 0, KING), (4, 9, -KING), (0, 0, ROOK),
        (0, 7, -CANNON), (4, 4, KNIGHT), (4, 5, -PAWN),
    ]:
        board[sq(file, rank)] = piece
    # 红吃炮(4.5)，黑卒过河吃马(4)：红净赚0.5，不是净弃子。
    # 若把黑卒过河后的价值增长当作吃子损失，会错误得到红净亏0.5。
    line = make_line(Position.from_fen(Position(board).fen()), ("a0a7", "e5e4"), 1, ("e9f9",))
    engine = HistoryEngine()
    report = asyncio.run(check_trap(engine, line, Limit(depth=12)))
    assert not report.passed and "净弃子" in report.errors[0]
    assert not engine.calls


def test_a_non_capture_response_is_not_an_accepted_sacrifice():
    # 第十半回合马2进1没有吃马/炮/车，不能把前期误着当作接受弃子。
    position = Position.from_fen(BASE.fens[0])
    for move in BASE.moves[:9]:
        position.push(parse_iccs(move))
    alternate = move_to_iccs(next(
        move for move in position.legal_moves() if move_to_iccs(move) != BASE.moves[9]
    ))
    line = make_line(Position.from_fen(BASE.fens[0]), BASE.moves, 9, (alternate,))
    report = asyncio.run(check_trap(HistoryEngine(), line, Limit(depth=12)))
    assert not report.passed and any("净弃子" in error for error in report.errors)


@pytest.mark.parametrize("kind", ["no_lines", "illegal", "mismatch", "bound", "wdl", "missing"])
def test_invalid_engine_results_never_pass(kind):
    def invalid(result):
        info = result.lines[0]
        if kind == "no_lines":
            return AnalysisResult([], None)
        if kind == "illegal":
            return AnalysisResult([replace(info, pv=["a0a9"])], "a0a9")
        if kind == "mismatch":
            return AnalysisResult([info], "a0a9")
        if kind == "bound":
            return AnalysisResult([replace(info, bound="lower")], result.bestmove)
        if kind == "wdl":
            return AnalysisResult([replace(info, wdl=(1001, 0, -1))], result.bestmove)
        return AnalysisResult([replace(info, wdl=None)], result.bestmove)

    report = asyncio.run(check_trap(HistoryEngine(invalid=invalid), BASE, Limit(depth=12)))
    assert not report.passed and report.failure_kind == "engine"


def test_engine_timeout_and_ignored_searchmove_never_pass():
    class TimeoutEngine(HistoryEngine):
        async def analyse(self, *args, **kwargs):
            raise TimeoutError("搜索超时")

    class IgnoringEngine(HistoryEngine):
        async def analyse(self, fen, moves=(), *, limit, multipv, searchmoves=()):
            return await super().analyse(fen, moves, limit=limit, multipv=multipv)

    for engine in (TimeoutEngine(), IgnoringEngine()):
        report = asyncio.run(check_trap(engine, BASE, Limit(depth=12)))
        assert not report.passed and report.failure_kind == "engine"


def stub_check(*, sacrifice=0.5, drop=0.0, main=1.0, refute=0.5, failure=None):
    passed = sacrifice >= 0.45 and drop <= 0.1 and main >= 0.75 and refute >= 0.45 and not failure
    return TrapCheck(
        BASE.trap.id, BASE.trap.name, passed, sacrifice, drop, main, refute, refute,
        () if passed else ("评分未达标",), failure_kind=failure,
    )


@pytest.mark.parametrize(
    ("reports", "status", "times"),
    [
        ([stub_check(), stub_check()], "passed", [5000, 15000]),
        ([stub_check(main=0.5), stub_check(main=0.5)], "rejected", [5000, 15000]),
        ([stub_check(sacrifice=0.46)] * 3, "passed", [5000, 15000, 30000]),
        ([stub_check(main=0.5), stub_check(), stub_check()], "passed", [5000, 15000, 30000]),
        ([stub_check(), stub_check(main=0.5), stub_check()], "pending", [5000, 15000, 30000]),
        ([stub_check(failure="engine")] * 2, "pending", [5000, 15000]),
        # 总结论都失败，但不同门槛变动仍须复核；15/30 未稳定时不能直接否决。
        ([stub_check(main=0.5), stub_check(main=0.5, sacrifice=0.4), stub_check(main=0.5)],
         "pending", [5000, 15000, 30000]),
    ],
)
def test_adaptive_verify_retains_each_round_and_rechecks_instability(
    monkeypatch, reports, status, times,
):
    mock = AsyncMock(side_effect=reports)
    monkeypatch.setattr(checker, "check_trap", mock)
    result = asyncio.run(verify_trap(HistoryEngine(), BASE))
    assert result.status == status and result.passed == (status == "passed")
    assert [item.movetime_ms for item in result.rounds] == times
    assert result.final is reports[-1]
    assert [call.args[2].movetime_ms for call in mock.call_args_list] == times
    assert asdict(result)["rounds"][0]["check"]["main_score"] == reports[0].main_score


def test_cli_verify_enforces_search_resources_and_closes_service(monkeypatch):
    from xiangqi.config import AppConfig
    from xiangqi.engine import EngineConfig

    configurations = []
    services = []
    fake_engine = HistoryEngine()
    fake_engine.name = "Pikafish fixture"

    class Service:
        def __init__(self, config):
            configurations.append(config)
            services.append(self)
            self.closed = False

        async def reviewer(self):
            return fake_engine

        async def close(self):
            self.closed = True

    monkeypatch.setattr(checker, "EngineService", Service)
    monkeypatch.setattr(checker, "verify_trap", AsyncMock(return_value="evidence"))
    monkeypatch.setattr(
        "xiangqi.config.load_config",
        lambda path: AppConfig(engine=EngineConfig(
            Path("/test/pikafish"), threads=8, hash_mb=1024,
        )),
    )
    name, reports = asyncio.run(checker._run(None, [BASE.trap.id], 50, verify=True))
    assert name == fake_engine.name and reports == ["evidence"]
    assert configurations[0].threads == 4 and configurations[0].hash_mb == 256
    assert services[0].closed


@pytest.fixture
def candidate_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_trap_candidates.py"
    spec = importlib.util.spec_from_file_location("candidate_script_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("binary_sha256", "different-engine"),
        ("network_sha256", "different-network"),
        ("threads", 8),
        ("hash_mb", 1024),
        ("checker_sha256", "different-checker"),
        ("script_sha256", "different-script"),
    ],
)
def test_candidate_cache_rejects_changed_engine_or_search_policy(candidate_script, field, changed):
    metadata = {
        "binary_sha256": "binary", "network_sha256": "network", "threads": 4,
        "hash_mb": 256, "checker_sha256": "checker", "script_sha256": "script",
    }
    report = {
        "status": "passed", "engine": metadata.copy(), "policy_version": 2,
        "input_fingerprint": "source-and-main", "main_kind": "engine_main",
    }
    assert candidate_script.cache_valid(report, metadata, "source-and-main", "engine_main")
    report["engine"][field] = changed
    assert not candidate_script.cache_valid(report, metadata, "source-and-main", "engine_main")


def test_candidate_cache_rejects_source_only_policy_and_baseline_line_changes(candidate_script):
    metadata = {
        "binary_sha256": "binary", "network_sha256": "network", "threads": 4,
        "hash_mb": 256, "checker_sha256": "checker", "script_sha256": "script",
    }
    report = {
        "status": "rejected", "engine": metadata, "policy_version": 2,
        "input_fingerprint": "input", "main_kind": "source_main",
    }
    assert not candidate_script.cache_valid(report, metadata, "input", "engine_main")
    report["main_kind"] = "engine_main"
    assert not candidate_script.cache_valid(report, metadata, "changed-source", "engine_main")
    report["policy_version"] = 1
    assert not candidate_script.cache_valid(report, metadata, "input", "engine_main")
    assert candidate_script.baseline_input(BASE) != candidate_script.baseline_input(
        replace(BASE, refute=tuple(reversed(BASE.refute))),
    )
    assert candidate_script.baseline_input(BASE) != candidate_script.baseline_input(
        replace(BASE, moves=BASE.moves[:-1]),
    )


def test_baseline_retry_preserves_pending_and_allows_only_one_attempt(
    candidate_script, monkeypatch, tmp_path,
):
    metadata = {
        "binary_sha256": "binary", "network_sha256": "network", "threads": 4,
        "hash_mb": 256, "checker_sha256": "checker",
    }
    invalid = replace(
        stub_check(failure="engine"), errors=("引擎只给出了分数边界",),
    )
    original = {
        "id": BASE.trap.id, "status": "pending", "engine": metadata,
        "main_iccs": list(BASE.moves), "proposed_trap": asdict(BASE.trap),
        "audit_log": "qima-shisan.searches.jsonl",
        "verification": {"rounds": [
            {"movetime_ms": 5000, "check": asdict(invalid)},
            {"movetime_ms": 15000, "check": asdict(stub_check())},
            {"movetime_ms": 30000, "check": asdict(stub_check())},
        ]},
    }
    (tmp_path / "qima-shisan.json").write_text(json.dumps(original))
    (tmp_path / "qima-shisan.searches.jsonl").write_text('previous-bound-output\n')
    monkeypatch.setattr(candidate_script, "DATA", tmp_path)
    verified = checker.TrapVerification(
        BASE.trap.id, BASE.trap.name, "passed",
        (checker.TrapVerificationRound(5000, stub_check()),), "通过",
    )
    check = AsyncMock(return_value=verified)
    monkeypatch.setattr(candidate_script, "verify_trap", check)
    report = asyncio.run(candidate_script.retry_baseline(HistoryEngine(), metadata))
    assert report["status"] == "passed" and report["first_verification_status"] == "pending"
    previous = json.loads((tmp_path / "qima-shisan.first-verification.json").read_text())
    assert previous == json.loads(json.dumps(original))
    assert (tmp_path / "qima-shisan.first-verification.searches.jsonl").read_text() == (
        'previous-bound-output\n'
    )
    assert (tmp_path / "qima-shisan.searches.jsonl").exists()
    with pytest.raises(ValueError, match="已经执行过一次"):
        asyncio.run(candidate_script.retry_baseline(HistoryEngine(), metadata))
    assert check.await_count == 1


def engine_main_fixture(candidate_script):
    candidate = replace(candidate_script.CANDIDATES[0], trap_ply=18, side=BLACK)
    source = {"html_sha256": "unit-test-source", "url": "https://example.org/fixture"}
    after = Position.start()
    for text in BASE.moves[:18]:
        after.push(parse_iccs(text))
    alternate = next(move for move in after.legal_moves() if move_to_iccs(move) != BASE.moves[18])
    from xiangqi.core import move_to_chinese

    refute = [move_to_chinese(after.board, alternate)]
    original = {
        "status": "rejected", "source": source, "thresholds": {},
        "proposed_trap": {"refute": refute}, "refute_generation": [{"pv": ["fixture"]}],
        "verification": {"rounds": [{"movetime_ms": 30000, "check": {"evidence": [{
            "label": "接受弃子后", "moves": list(BASE.moves[:19]), "side": BLACK,
            "pv": list(BASE.moves[19:]),
        }]}}]},
    }
    return candidate, source, original


def test_engine_main_cache_fingerprint_includes_actual_pv_refute_and_generation(candidate_script):
    candidate, source, original = engine_main_fixture(candidate_script)
    previous = candidate_script.engine_main_input(candidate, BASE.moves, source, original)
    changed = deepcopy(original)
    changed["verification"]["rounds"][0]["check"]["evidence"][0]["pv"][1] = "a0a1"
    assert previous != candidate_script.engine_main_input(candidate, BASE.moves, source, changed)
    changed = deepcopy(original)
    changed["proposed_trap"]["refute"] = ["different-refute"]
    assert previous != candidate_script.engine_main_input(candidate, BASE.moves, source, changed)
    changed = deepcopy(original)
    changed["refute_generation"][0]["pv"] = ["different-generation"]
    assert previous != candidate_script.engine_main_input(candidate, BASE.moves, source, changed)


@pytest.mark.parametrize("kind", ["illegal_second_ply", "short_nonterminal", "early_terminal"])
def test_engine_main_requires_complete_legal_pv_or_real_early_terminal(
    candidate_script, monkeypatch, tmp_path, kind,
):
    candidate, source, original = engine_main_fixture(candidate_script)
    accepted = original["verification"]["rounds"][0]["check"]["evidence"][0]
    if kind == "illegal_second_ply":
        accepted["pv"] = [BASE.moves[19], *("z9z0",) * 11]
    elif kind == "short_nonterminal":
        accepted["pv"] = [BASE.moves[19]]
    else:
        accepted["pv"] = [*BASE.moves[19:], *("z9z0",) * 6]
    monkeypatch.setattr(candidate_script, "DATA", tmp_path)
    monkeypatch.setattr(candidate_script, "parse_source", lambda item: (BASE.moves, source))
    verified = checker.TrapVerification(
        candidate.id, candidate.name, "rejected",
        (checker.TrapVerificationRound(15000, stub_check(main=0.5)),), "未达标",
    )
    check = AsyncMock(return_value=verified)
    monkeypatch.setattr(candidate_script, "verify_trap", check)
    report = asyncio.run(candidate_script.verify_engine_main(
        HistoryEngine(), candidate, {}, original,
    ))
    if kind == "early_terminal":
        assert check.await_count == 1 and report["generation"]["actual_pv_plies"] == 6
        assert report["generation"]["stop_reason"].startswith("提前终局")
    else:
        assert report["status"] == "pending" and check.await_count == 0
        assert "published_trap" not in report


@pytest.fixture
def hidden_script(candidate_script, monkeypatch):
    monkeypatch.setitem(sys.modules, "verify_trap_candidates", candidate_script)
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_hidden_traps.py"
    spec = importlib.util.spec_from_file_location("hidden_script_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


HIDDEN_SHUNPAO_MOVES = tuple((
    "h2e2 h7e7 h0g2 h9g7 c3c4 g6g5 i0h0 b9c7 b0c2 b7b3 c2d4 b3g3 "
    "b2c2 a9b9 c4c5 b9b4 c2c6 b4d4 c6c9 d9e8 a0b0 i9h9 h0h9 g3g0 "
    "f0e1 g7h9 c5c6 e7g7 c6c7 g7g2 c9a9 g0i0 b0b9 e8d9 e1d2 d4d2 "
    "e2e6 g2g0 e0e1 e9e8 b9b8"
).split())


def test_hidden_source_has_exact_capture_point_and_source_defense_is_legal(
    hidden_script, monkeypatch,
):
    monkeypatch.setattr(hidden_script, "parse_source", lambda candidate: (
        HIDDEN_SHUNPAO_MOVES, {"html_sha256": "unit-test-source"},
    ))
    report, positions, steps = hidden_script.initial_report(hidden_script.ACTIVE)
    assert len(steps) == 41 and steps[16].cn == "炮七进四" and steps[17].cn == "车2平4"
    capture = parse_iccs(HIDDEN_SHUNPAO_MOVES[17])
    assert positions[17].board[capture[1]] == KNIGHT
    assert checker._material(positions[18], RED) < checker._material(positions[16], RED)
    refute = tuple(step["cn"] for step in report["source_text_refute"]["steps"])
    trap = hidden_script.build_hidden_trap(steps, refute)
    line = checker._replay(hidden_script.replay_trap(trap))
    assert len(line[0]) == 42 and len(line[1]) == 6
    assert trap.moves[-1] == "车八退一" and trap.trap_ply == 17
    assert report["status"] == "pending" and report["engine"] == {}


def test_hidden_prepare_only_records_three_sources_without_starting_engine(
    hidden_script, candidate_script, monkeypatch, tmp_path,
):
    monkeypatch.setattr(hidden_script, "DATA", tmp_path)
    monkeypatch.setattr(candidate_script, "DATA", tmp_path)
    recorded = []

    def initial(candidate):
        recorded.append(candidate.id)
        return {
            "id": candidate.id, "name": candidate.name, "status": "pending",
            "reason": "未搜索", "engine": {},
        }, [], ()

    monkeypatch.setattr(hidden_script, "initial_report", initial)
    monkeypatch.setattr(hidden_script, "EngineService", lambda config: pytest.fail("不得启动引擎"))
    asyncio.run(hidden_script.execute(SimpleNamespace(prepare_only=True)))
    assert set(recorded) == {hidden_script.ACTIVE.id, *(item.id for item in hidden_script.PENDING)}
    assert len(json.loads((tmp_path / "hidden-aggregate.json").read_text())["results"]) == 3
