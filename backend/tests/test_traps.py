"""飞刀数据缺失会阻止应用启动；覆盖合法棋谱、索引和对弈 API 的关键契约。"""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.core import RED, Position, game_result, parse_iccs
from xiangqi.engine import AnalysisResult, InfoLine, Limit
from xiangqi.openings import TRAP_LINES, TRAPS, build_trap_index, match_move, trap_advice
from xiangqi.openings.check import check_trap
from xiangqi.openings.index import load_lines, match_position, replay_trap

TRAP_ID = "qima-shisan"


def test_public_source_main_line_and_refute_are_legal():
    line = TRAP_LINES[TRAP_ID]
    # 公开古谱 25 个半回合，末步炮八平五，而非任意拼接后声称是古谱。
    source = (
        "h2e2 h7e7 h0g2 h9g7 i0i1 i9h9 i1d1 h9h3 d1d8 b9a7 "
        "a0a1 b7b0 b2b7 g7h9 e2e6 f9e8 a1d1 e9f9 d8d9 e8d9 "
        "d1f1 e7f7 f1f7 f9e9 b7e7"
    )
    assert line.moves == tuple(source.split())
    pos = Position.start()
    for index, text in enumerate(line.moves, start=1):
        pos.push(parse_iccs(text))
        assert pos.fen() == line.fens[index]
    assert game_result(pos).winner == RED
    assert game_result(pos).reason == "checkmate"
    assert line.trap.source.startswith("https://www.xqipu.com/qipu/")
    defense = Position.from_fen(line.trap_fen)
    for text, step in zip(line.refute, line.refute_steps, strict=True):
        defense.push(parse_iccs(text))
        assert defense.fen() == step.fen
    assert line.refute_steps[0].ply == line.trap.trap_ply + 1
    assert game_result(defense) is None


def test_illegal_main_refute_and_duplicate_ids_are_rejected():
    trap = TRAPS[0]
    with pytest.raises(ValueError, match="主变 第 1 步"):
        replay_trap(replace(trap, moves=("车一进九", *trap.moves[1:])))
    with pytest.raises(ValueError, match="破解变 第 12 步"):
        replay_trap(replace(trap, refute=("将5进5",)))
    with pytest.raises(ValueError, match="重复的飞刀 id"):
        load_lines((trap, trap))


def test_index_ignores_move_counters_but_not_turn_or_board():
    line, index = TRAP_LINES[TRAP_ID], build_trap_index()
    fields = line.trap_fen.split()
    fields[-2:] = ["72", "85"]
    pos = Position.from_fen(" ".join(fields))
    match = match_position(index, pos)
    assert match is not None and match.trap.id == TRAP_ID and match.ply == 11
    fields[1] = "w"
    assert match_position(index, Position.from_fen(" ".join(fields))) is None
    assert match_position(index, Position.start()) is None


def test_matching_moves_and_advice_do_not_mutate_position():
    line, index = TRAP_LINES[TRAP_ID], build_trap_index()
    pos = Position.from_fen(line.start_fen)
    fen = pos.fen()
    match = match_move(index, pos, line.moves[10])
    assert match is not None and match.ply == 11
    assert pos.fen() == fen
    advice = trap_advice(pos, match_position(index, pos))
    assert advice.kind == "suggest" and "车九进一" in advice.advice
    pos.push(parse_iccs(line.moves[10]))
    warning = trap_advice(pos, match_position(index, pos))
    assert warning.kind == "warn" and "士6进5" in warning.advice
    assert match_move(index, pos, "bad") is None
    assert match_move(index, pos, "a0a9") is None
    pos.push(parse_iccs(line.refute[0]))
    assert match_position(index, pos) is None  # 走对防守后不再误报为主变陷阱


def test_traps_api_and_free_game_preset():
    line = TRAP_LINES[TRAP_ID]
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        summaries = client.get("/api/traps")
        assert summaries.status_code == 200 and len(summaries.json()) == len(TRAPS)
        summary = next(item for item in summaries.json() if item["id"] == TRAP_ID)
        assert summary["ply_count"] == 25 and summary["trap_ply"] == 11
        detail = client.get(f"/api/traps/{TRAP_ID}").json()
        assert detail["trap_fen"] == line.trap_fen and detail["start_fen"] == line.start_fen
        assert detail["moves"][-1]["cn"] == "炮八平五"
        assert detail["refute"][0]["cn"] == "士6进5"
        assert detail["refute"][0]["ply"] == 12
        assert client.get("/api/traps/missing").status_code == 404
        game = client.post("/api/games", json={"trap_id": TRAP_ID}).json()
        assert game["position"]["fen"] == line.start_fen
        assert len(game["moves"]) == 10 and game["book_name"] == line.trap.name
        assert client.post("/api/games", json={"trap_id": "random"}).status_code == 200


class ScoreEngine:
    """返回故意可控的分数；检查器必须拒绝没有胜势/有效破解的数据。"""

    def __init__(self, refute_score=0.5):
        self.refute_score = refute_score
        self.calls = []

    async def analyse(self, fen, moves=(), *, limit, multipv, searchmoves=()):
        pos = Position.from_fen(fen)
        for text in moves:
            pos.push(parse_iccs(text))
        fen = pos.fen()
        self.calls.append((fen, searchmoves))
        move = searchmoves[0] if searchmoves else TRAP_LINES[TRAP_ID].refute[0]
        if not pos.is_legal(parse_iccs(move)):
            from xiangqi.core import move_to_iccs

            move = move_to_iccs(pos.legal_moves()[0])
        score = self.refute_score if fen == TRAP_LINES[TRAP_ID].refute_steps[-1].fen else 0.5
        w = round(score * 1000)
        return AnalysisResult([InfoLine(1, 20, [move], wdl=(w, 0, 1000 - w))], move)


def test_checker_uses_real_search_results_and_rejects_failed_refute():
    good = ScoreEngine()
    report = asyncio.run(check_trap(good, TRAP_LINES[TRAP_ID], Limit(depth=12)))
    assert report.passed and report.main_score == 1.0  # 规则已证明主变终点将死
    assert len(good.calls) == 10
    assert len(report.defense_checks) == 3
    assert report.accepted_score == 0.5
    assert any(call[1] == ("a0a1",) for call in good.calls)
    assert any(call[1] == ("f9e8",) for call in good.calls)
    bad = asyncio.run(check_trap(ScoreEngine(0.2), TRAP_LINES[TRAP_ID], Limit(depth=12)))
    assert not bad.passed and any("破解变终点" in error for error in bad.errors)
