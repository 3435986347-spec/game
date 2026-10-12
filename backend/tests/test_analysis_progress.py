"""真实 UCI 进度传输：累计计数不求和，重复快照不重复推送，缓存不伪装新搜索。"""

import asyncio
from dataclasses import replace

from xiangqi.api import analysis
from xiangqi.api.games import _hint_view
from xiangqi.core import Position
from xiangqi.engine import AnalysisCache, AnalysisResult, InfoLine


def test_analysis_and_hint_do_not_call_unfinished_exchange_a_trap():
    position = Position.from_fen("r3k4/9/9/9/pRr6/R8/P8/9/C8/3K5 w - - 0 1")
    pv = ["a4a5", "a9a5", "b5a5", "c5a5", "a1a5"]
    line = InfoLine(1, 32, pv, wdl=(200, 800, 0))
    extras, _ = analysis._line_extras(position, [line], {})
    assert extras[0][1] is None
    reply = AnalysisResult([InfoLine(1, 32, pv[1:], wdl=(0, 800, 200))], None)
    hint = _hint_view(position, line, 3, reply=reply, trap_index={})
    assert hint.trap is None
    assert "弃子" not in hint.purpose


def test_analysis_and_hint_do_not_attribute_future_sacrifice_to_current_move():
    position = Position.start()
    pv = ["h2e2", "h9g7", "e2e6", "g7e6"]
    line = InfoLine(1, 32, pv, wdl=(200, 800, 0))
    extras, _ = analysis._line_extras(position, [line], {})
    assert extras[0][1] is None
    reply = AnalysisResult([InfoLine(1, 32, pv[1:], wdl=(0, 800, 200))], None)
    hint = _hint_view(position, line, 3, reply=reply, trap_index={})
    assert hint.trap is None
    assert "弃子" not in hint.purpose


def test_analysis_and_hint_keep_actual_supported_sacrifice():
    from conftest import play

    position = play(Position.start(), "h2e2", "h9g7")
    line = InfoLine(1, 32, ["e2e6", "g7e6"], wdl=(200, 800, 0))
    extras, _ = analysis._line_extras(position, [line], {})
    assert extras[0][1]["id"] == "sacrifice"
    reply = AnalysisResult([InfoLine(1, 32, ["g7e6"], wdl=(0, 800, 200))], None)
    hint = _hint_view(position, line, 3, reply=reply, trap_index={})
    assert hint.trap.id == "sacrifice"


def test_named_traps_keep_offer_of_preexisting_hanging_piece():
    from xiangqi.core import parse_iccs
    from xiangqi.openings import TRAP_LINES, build_trap_index

    index = build_trap_index(TRAP_LINES)
    for trap_id, trap in TRAP_LINES.items():
        position = Position.start()
        decision = trap.trap.trap_ply - 1
        for text in trap.moves[:decision]:
            position.push(parse_iccs(text))
        line = InfoLine(1, 32, list(trap.moves[decision:]), wdl=(200, 800, 0))
        extras, _ = analysis._line_extras(position, [line], index, trap_id)
        assert extras[0][1]["id"] == trap_id


def test_multipv_reports_cumulative_max_and_does_not_invent_missing_values():
    lines = [
        InfoLine(1, 26, ["h0g2"], nodes=1000, time_ms=200, nps=5000),
        InfoLine(2, 25, ["b0c2"], nodes=1500, time_ms=300, nps=6000),
    ]
    assert analysis._search_stats(lines) == {
        "depth": 26, "nodes": 1500, "elapsed_ms": 300, "nps": 6000,
    }
    assert analysis._search_stats([InfoLine(1, 20, ["h0g2"])]) == {
        "depth": 20, "nodes": None, "elapsed_ms": None, "nps": None,
    }


class RecordingSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class RepeatingEngine:
    async def analyse_infinite(self, *args, **kwargs):
        first = InfoLine(1, 25, ["h0g2"], score_cp=20, nodes=1000, time_ms=200, nps=5000)
        yield [first]
        yield [first]
        yield [first]
        # PV、评分和深度不变，但搜索工作量确实增加：应更新进度。
        yield [replace(first, nodes=2000, time_ms=400)]


class EngineService:
    async def analyser(self):
        return RepeatingEngine()


def test_identical_snapshots_are_skipped_but_real_progress_and_completion_are_sent(monkeypatch):
    monkeypatch.setattr(analysis, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(analysis, "_MAX_STALE", 0)
    socket = RecordingSocket()
    position = Position.start()
    target = analysis._Target(None, position.fen(), [], position)
    asyncio.run(analysis._stream(socket, EngineService(), target, 1, {}, AnalysisCache()))
    assert len(socket.messages) == 3
    first, progress, complete = socket.messages
    assert first["lines"] == progress["lines"]
    assert first["search"]["nodes"] == 1000
    assert progress["search"]["nodes"] == 2000
    assert complete["status"] == "complete"


def test_cached_result_has_no_live_search_statistics(monkeypatch):
    monkeypatch.setattr(analysis, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(analysis, "_MAX_STALE", 0)
    socket = RecordingSocket()
    position = Position.start()
    cache = AnalysisCache()
    cached = InfoLine(1, 32, ["h0g2"], score_cp=20, nodes=900000, time_ms=90000, nps=10000)
    cache.put(position.fen(), [], 1, None, AnalysisResult([cached], None))
    target = analysis._Target(None, position.fen(), [], position)
    asyncio.run(analysis._stream(socket, EngineService(), target, 1, {}, cache))
    seed, live = socket.messages[:2]
    assert seed["seeded"] and seed["depth"] == 32 and seed["search"] is None
    assert not live.get("seeded")
    assert live["search"]["nodes"] == 1000 and live["search"]["elapsed_ms"] == 200


def test_no_moves_is_complete_and_has_no_fake_search_statistics():
    position = Position.from_fen("R3k4/R8/9/9/9/9/9/9/9/3K5 b - - 0 1")
    assert not position.legal_moves()
    socket = RecordingSocket()
    target = analysis._Target(None, position.fen(), [], position)
    asyncio.run(analysis._stream(socket, None, target, 1, {}, AnalysisCache()))
    message = socket.messages[0]
    assert message["status"] == "complete" and message["search"] is None


def test_realtime_pv_preserves_legal_tail_beyond_ten_moves_and_stops_at_illegal_move():
    from xiangqi.openings import TRAP_LINES

    line = TRAP_LINES["qima-shisan"]
    pv = [*line.moves, "a0a9"]  # 将死后的伪造续着必须被截掉。
    message = asyncio.run(analysis._info_message(
        None, 0, Position.start(), [InfoLine(1, 32, pv, score_cp=20)], {},
    ))
    displayed = message["lines"][0]["pv_cn"]
    assert len(displayed) == len(line.moves) > 10
    assert displayed[-1] == "炮八平五"
