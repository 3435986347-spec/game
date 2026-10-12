"""训练讲解采用复盘的同次候选评分，并共享真实引擎分析缓存。"""

import asyncio
from types import SimpleNamespace

import pytest

from xiangqi.api.train import _explain
from xiangqi.core import START_FEN, Position, RuleConfig, move_to_iccs, parse_iccs, parse_move
from xiangqi.core.rules import GameResult
from xiangqi.engine import AnalysisCache, AnalysisResult, InfoLine


def wdl_for_score(score):
    wins, draws = divmod(round(score * 2000), 2)
    return wins, draws, 1000 - wins - draws


class Engine:
    def __init__(
        self, *, best="a0a1", candidate="h2e2", candidate_score=0.59,
        best_score=0.60, with_reply=False, invalid_reply=False,
    ):
        self.best = best
        self.candidate = candidate
        self.candidate_score = candidate_score
        self.best_score = best_score
        self.with_reply = with_reply
        self.invalid_reply = invalid_reply
        self.calls = []

    async def analyse(self, fen, moves, *, limit, multipv):
        self.calls.append((fen, tuple(moves), limit, multipv))
        if not moves:
            legal = [move_to_iccs(move) for move in Position.from_fen(fen).legal_moves()]
            third = next(move for move in legal if move not in (self.best, self.candidate))
            alternatives = (self.best, self.candidate, third)
            scores = (self.best_score, self.candidate_score, 0.40)
        else:
            position = Position.from_fen(fen)
            for text in moves:
                position.push(parse_iccs(text))
            alternatives = (move_to_iccs(position.legal_moves()[0]),)
            scores = (0.60,)  # 独立搜索给红方0.40，不能覆盖前局面已搜出的本着法评分。
        def pv(move):
            if not self.with_reply or moves:
                return [move]
            if self.invalid_reply:
                return [move, "a0a0"]
            pos = Position.from_fen(fen)
            pos.push(parse_iccs(move))
            texts = [move]
            for _ in range(4):
                legal = pos.legal_moves()
                if not legal:
                    break
                reply = legal[0]
                texts.append(move_to_iccs(reply))
                pos.push(reply)
            return texts

        lines = [
            InfoLine(rank, 20, pv(move), wdl=wdl_for_score(score))
            for rank, (move, score) in enumerate(zip(alternatives, scores, strict=True), start=1)
        ]
        return AnalysisResult(lines, lines[0].move)


class Explainer:
    level = "入门"

    async def explain(self, ctx):
        # 捕获真实讲解上下文；不访问远程大模型或用户密钥。
        return SimpleNamespace(
            to_dict=lambda: {
                "grade": ctx.grade,
                "win_before": ctx.win_before,
                "win_after": ctx.win_after,
                "best": ctx.best[0][0],
                "move": ctx.move_cn,
                "reply": ctx.data.get("opponent_reply"),
                "allowed": ctx.allowed,
            }
        )


def request_for(engine):
    async def reviewer():
        return engine

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                engines=SimpleNamespace(reviewer=reviewer),
                config=SimpleNamespace(review_movetime_ms=1500, rules=RuleConfig()),
                analysis_cache=AnalysisCache(),
                explainer=Explainer(),
            )
        )
    )


def test_best_move_is_not_downgraded_by_independent_reply_search():
    engine = Engine()
    request = request_for(engine)
    result = asyncio.run(_explain(request, START_FEN, "a0a1", judge_grade=True))
    assert result["best"] == result["move"] == "车九进一"
    assert result["grade"] == "好棋"
    assert result["win_before"] == result["win_after"] == 0.60


def test_known_candidate_uses_its_score_from_the_same_search():
    engine = Engine()
    result = asyncio.run(_explain(request_for(engine), START_FEN, "h2e2", judge_grade=True))
    assert result["grade"] == "好棋"
    assert result["win_before"] == 0.60 and result["win_after"] == 0.59


def test_black_best_move_preserves_the_movers_score_perspective():
    position = Position.start()
    position.push(parse_iccs("h2e2"))
    engine = Engine(best="a9a8", candidate="h7e7")
    result = asyncio.run(_explain(request_for(engine), position.fen(), "a9a8", judge_grade=True))
    assert result["best"] == result["move"] == "车1进1"
    assert result["grade"] == "好棋"
    assert result["win_before"] == result["win_after"] == 0.60


def test_terminal_result_takes_priority_over_candidate_score():
    fen = "4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1"
    engine = Engine(best="b1b9", candidate="b1e1")
    result = asyncio.run(_explain(request_for(engine), fen, "b1b9", judge_grade=True))
    assert result["win_after"] == 1.0
    assert result["grade"] == "好棋"
    assert len(engine.calls) == 1  # 终局由规则确定，免掉对手搜索。


def test_training_reuses_complete_cached_analysis_without_extra_search():
    engine = Engine()
    request = request_for(engine)

    async def scenario():
        first = await _explain(request, START_FEN, "h2e2", judge_grade=True)
        second = await _explain(request, START_FEN, "h2e2", judge_grade=True)
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second
    assert len(engine.calls) == 2
    assert all(call[2].movetime_ms == 1500 for call in engine.calls)
    assert [call[3] for call in engine.calls] == [3, 1]


def test_solution_explanation_keeps_grade_optional():
    result = asyncio.run(_explain(request_for(Engine()), START_FEN, "a0a1", judge_grade=False))
    assert result["grade"] is None


@pytest.mark.parametrize("black", [False, True])
@pytest.mark.parametrize("judge_grade", [False, True])
def test_training_reuses_candidate_reply_and_keeps_score_perspective(black, judge_grade):
    position = Position.start()
    if black:
        position.push(parse_iccs("h2e2"))
    best, candidate = ("a9a8", "h7e7") if black else ("a0a1", "h2e2")
    engine = Engine(best=best, candidate=candidate, with_reply=True)
    request = request_for(engine)
    result = asyncio.run(_explain(request, position.fen(), candidate, judge_grade=judge_grade))
    assert result["win_after"] == 0.59
    assert result["grade"] == ("好棋" if judge_grade else None)
    assert len(engine.calls) == 1
    assert sum(call[2].movetime_ms for call in engine.calls) == 1500
    reply = result["reply"]
    assert reply and reply["cn"] in result["allowed"]
    position.push(parse_iccs(candidate))
    for text in reply["pv_cn"]:
        move = parse_move(position.board, position.turn, text)
        assert position.is_legal(move)
        assert text in result["allowed"]
        position.push(move)


def test_candidate_reply_reuse_combines_with_completed_before_cache():
    engine = Engine(with_reply=True)
    request = request_for(engine)

    async def scenario():
        first = await _explain(request, START_FEN, "h2e2", judge_grade=True)
        second = await _explain(request, START_FEN, "h2e2", judge_grade=True)
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second
    assert len(engine.calls) == 1
    assert request.app.state.analysis_cache.get(START_FEN, ["h2e2"], 1, 1500) is None


@pytest.mark.parametrize("missing", ["candidate", "reply", "illegal_reply"])
def test_training_keeps_after_search_when_candidate_reply_is_unavailable(missing):
    engine = Engine(with_reply=missing != "reply", invalid_reply=missing == "illegal_reply")
    move = "i0i1" if missing == "candidate" else "h2e2"
    result = asyncio.run(_explain(request_for(engine), START_FEN, move, judge_grade=True))
    assert result["reply"]
    assert len(engine.calls) == 2
    assert sum(call[2].movetime_ms for call in engine.calls) == 3000


@pytest.mark.parametrize("black", [False, True])
def test_natural_move_limit_draw_overrides_best_move_immunity(black):
    position = Position.start()
    position.halfmove_clock = 1
    position.turn = -1 if black else 1
    best, candidate = ("a9a8", "h7e7") if black else ("a0a1", "h2e2")
    engine = Engine(best=best, candidate=candidate, best_score=0.90, with_reply=True)
    request = request_for(engine)
    request.app.state.config.rules = RuleConfig(move_limit=1)
    result = asyncio.run(_explain(request, position.fen(), best, judge_grade=True))
    assert result["win_before"] == 0.90 and result["win_after"] == 0.50
    assert result["grade"] == "漏着"
    assert result["reply"] is None
    assert len(engine.calls) == 1


@pytest.mark.parametrize("black", [False, True])
@pytest.mark.parametrize("outcome", ["win", "draw", "loss"])
def test_rule_terminal_overrides_candidate_score_and_brilliance(monkeypatch, black, outcome):
    from xiangqi.training import review

    position = Position.start()
    if black:
        position.push(parse_iccs("h2e2"))
    side = position.turn
    best, candidate = ("a9a8", "h7e7") if black else ("a0a1", "h2e2")
    engine = Engine(best=best, candidate=candidate, best_score=0.90, with_reply=True)
    winner = {"win": side, "draw": None, "loss": -side}[outcome]
    monkeypatch.setattr(
        review, "game_result",
        lambda pos, rules: GameResult(winner, "perpetual_check") if pos.ply else None,
    )
    result = asyncio.run(_explain(request_for(engine), position.fen(), best, judge_grade=True))
    assert result["win_after"] == {"win": 1.0, "draw": 0.5, "loss": 0.0}[outcome]
    assert result["grade"] == ("好棋" if outcome == "win" else "漏着")
    assert result["reply"] is None
    assert len(engine.calls) == 1


@pytest.mark.parametrize("black", [False, True])
@pytest.mark.parametrize("above", [False, True])
@pytest.mark.parametrize(
    "boundary,expected,higher",
    [
        (0.02, "好棋", "可以"),
        (0.05, "可以", "缓着"),
        (0.10, "缓着", "失误"),
        (0.20, "失误", "漏着"),
    ],
)
def test_training_uses_the_shared_boundary_rules_for_red_and_black(
    black, above, boundary, expected, higher
):
    position = Position.start()
    if black:
        position.push(parse_iccs("h2e2"))
    best, played = ("a9a8", "h7e7") if black else ("a0a1", "h2e2")
    # WDL 的可表示步长为 0.0005，覆盖恰好在界上与真实略超界两种情况。
    score = 0.6 - boundary - (0.0005 if above else 0)
    engine = Engine(best=best, candidate=played, candidate_score=score)
    result = asyncio.run(_explain(request_for(engine), position.fen(), played, judge_grade=True))
    assert result["grade"] == (higher if above else expected)
    assert result["win_before"] == 0.6
    assert result["win_after"] == round(score, 4)
