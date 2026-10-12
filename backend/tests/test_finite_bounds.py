"""有限搜索末尾的未确认边界不能进入缓存、飞刀验证或训练评分。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.core import START_FEN, Position
from xiangqi.engine import AnalysisCache, AnalysisResult, EngineError, InfoLine, Limit, UciEngine
from xiangqi.openings.check import _score
from xiangqi.training import judge_moves, lines_from


class ScriptedEngine(UciEngine):
    """用真实 analyse / UCI 解析链路读取固定输出，不启动外部引擎。"""

    def __init__(self, output):
        super().__init__(["unused"])
        self.output = output
        self.searches = 0

    def _ensure_alive(self):
        pass

    async def _prepare(self, *args):
        self.searches += 1
        self.remaining = iter(self.output)

    def _send(self, command):
        pass

    async def _readline(self, timeout):
        return next(self.remaining)


def pending_bound_engine(bound="upper"):
    return ScriptedEngine([
        "info depth 8 score cp 400 time 100 pv a0a1",
        f"info depth 22 score cp -400 {bound}bound time 15000 pv a0a1",
        "bestmove a0a1",
    ])


@pytest.mark.parametrize("bound", ["upper", "lower"])
def test_finite_pending_bound_is_returned_and_cannot_enter_completed_cache(bound):
    async def scenario():
        engine = pending_bound_engine(bound)
        cache = AnalysisCache()
        for _ in range(2):
            result = await cache.get_or_analyse(
                engine, START_FEN, limit=Limit(movetime_ms=15000),
            )
            line = result.lines[0]
            assert result.bestmove == line.move == "a0a1"
            assert (line.depth, line.score_cp, line.time_ms, line.bound) == (
                22, -400, 15000, bound,
            )
            assert cache.get(START_FEN, [], 1, 15000) is None
        assert engine.searches == 2

    asyncio.run(scenario())


def test_trap_verifier_rejects_pending_bound_from_actual_finite_parser():
    async def scenario():
        evidence = []
        with pytest.raises(EngineError, match="分数边界"):
            await _score(
                pending_bound_engine(), Position.start(), 1, Limit(movetime_ms=15000),
                ("a0a1",), initial_fen=START_FEN, label="弃子验证", evidence=evidence,
            )
        assert evidence == []

    asyncio.run(scenario())


def test_grading_keeps_only_candidates_before_first_pending_bound():
    result = AnalysisResult([
        InfoLine(1, 20, ["a0a1"], score_cp=20),
        InfoLine(2, 22, ["h2e2"], score_cp=400, bound="lower"),
        InfoLine(3, 20, ["b2e2"], score_cp=0),
    ], "a0a1")
    assert [line.move for line in lines_from(result)] == ["a0a1"]
    result.lines = result.lines[1:]
    with pytest.raises(EngineError, match="已确认"):
        lines_from(result)
    with pytest.raises(EngineError, match="候选着法"):
        lines_from(AnalysisResult([], None))


def test_grading_does_not_relabel_gapped_candidates_as_continuous():
    result = AnalysisResult([
        InfoLine(1, 20, ["a0a1"], score_cp=20),
        InfoLine(3, 20, ["b2e2"], score_cp=0),
    ], "a0a1")
    assert [line.move for line in lines_from(result)] == ["a0a1"]


@pytest.mark.parametrize("output", [
    ["info depth 20 score cp 20 pv a0a1", "bestmove a0a1"],
    [
        "info depth 20 multipv 1 score cp 20 pv a0a1",
        "info depth 20 multipv 2 score cp 20 upperbound pv h2e2",
        "bestmove a0a1",
    ],
])
def test_judgement_rejects_missing_or_unconfirmed_restricted_candidates(output):
    async def scenario():
        engine = ScriptedEngine(output)
        with pytest.raises(EngineError, match="所有待比较着法"):
            await judge_moves(
                engine, START_FEN, [], ["h2e2", "b2e2"], limit=Limit(movetime_ms=1500),
            )
        assert engine.searches == 2  # 缺少候选先限定搜索；仍缺少则报错，不能判为错着。

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["bound", "missing"])
@pytest.mark.parametrize("kind", ["card", "puzzle"])
def test_failed_training_score_returns_503_without_recording_an_answer(tmp_path, failure, kind):
    engine = pending_bound_engine() if failure == "bound" else ScriptedEngine([
        "info depth 20 score cp 20 pv a0a1", "bestmove a0a1",
    ])

    async def reviewer():
        return engine

    app = create_app(AppConfig(static_dir=None, library_db=tmp_path / "training.db"))
    app.state.engines = SimpleNamespace(configured=True, reviewer=reviewer)
    with TestClient(app) as client:
        store = app.state.training
        if kind == "card":
            card_id = store.add_card("mistake", START_FEN, "h2e2", played="a3a4")
            url = f"/api/train/cards/{card_id}/answer"
            before = store.card(card_id)
        else:
            store.add_puzzles([{
                "fen": START_FEN, "solution": "h2e2", "pv": "", "tags": ["要着"],
                "rating": 1200, "source_game_id": None, "source_ply": 0, "master_found": 1,
            }])
            puzzle_id = client.get("/api/train/puzzle").json()["puzzle"]["id"]
            url = f"/api/train/puzzles/{puzzle_id}/attempt"
            before = store.puzzle(puzzle_id)
        summary_before = client.get("/api/train/summary").json()
        response = client.post(url, json={"move": "b2e2"})
        assert response.status_code == 503
        assert client.get("/api/train/summary").json() == summary_before
        assert (store.card(card_id) if kind == "card" else store.puzzle(puzzle_id)) == before
        # 精确命中题目正解无需引擎评分，失败后仍能正常作答。
        searches = engine.searches
        correct = client.post(url, json={"move": "h2e2"})
        assert correct.status_code == 200 and correct.json()["correct"]
        assert engine.searches == searches


def test_pending_first_score_rejects_training_explanation_without_saving(tmp_path):
    async def reviewer():
        return pending_bound_engine()

    app = create_app(AppConfig(static_dir=None, library_db=tmp_path / "training.db"))
    app.state.engines = SimpleNamespace(configured=True, reviewer=reviewer)
    with TestClient(app) as client:
        store = app.state.training
        card_id = store.add_card("mistake", START_FEN, "h2e2", played="a0a1")
        response = client.post(f"/api/train/cards/{card_id}/explain")
        assert response.status_code == 503
        assert store.card(card_id)["explanation"] is None


@pytest.mark.parametrize("failure", ["bound", "missing"])
def test_failed_guess_scoring_returns_503_without_advancing_or_recording_failure(tmp_path, failure):
    engine = pending_bound_engine() if failure == "bound" else ScriptedEngine([
        "info depth 20 score cp 20 pv a0a1", "bestmove a0a1",
    ])

    async def reviewer():
        return engine

    app = create_app(AppConfig(static_dir=None, library_db=tmp_path / "training.db"))
    app.state.engines = SimpleNamespace(configured=True, reviewer=reviewer)
    with TestClient(app) as client:
        game_id = app.state.library.save_game(
            initial_fen=START_FEN, moves=["h2e2", "h9g7"], result="*", headers={},
        )
        response = client.post("/api/guess", json={"game_id": game_id, "side": "red"})
        assert response.status_code == 200
        session_id = response.json()["id"]
        store = app.state.training
        before = store.session(session_id)
        summary_before = client.get("/api/train/summary").json()
        answer = client.post(f"/api/guess/{session_id}/answer", json={"move": "b2e2"})
        assert answer.status_code == 503
        assert store.session(session_id) == before
        assert store.answers(session_id) == []
        assert client.get("/api/train/summary").json() == summary_before
