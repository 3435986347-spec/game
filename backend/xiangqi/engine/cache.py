"""跨提示、复盘与实时分析共享的、有容量限制的局面搜索缓存。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache

from ..core import Position, parse_iccs
from .uci import AnalysisResult, InfoLine, Limit, UciEngine

_Key = tuple[str, tuple[str, ...], int, int | None]
_SearchKey = tuple[int, str, tuple[str, ...], int, int]


@dataclass
class _PendingSearch:
    task: asyncio.Task[AnalysisResult]
    waiters: int = 0


class AnalysisCache:
    """按起始 FEN 和完整着法历史复用搜索，不混淆重复局面的规则状态。

    有限搜索必须覆盖请求的 MultiPV 和时间预算才可复用；没有时间预算的
    实时快照只用来预填实时分析面板，不能充当已完成的有限搜索。
    同一搜索条件保留更深结果，容量满时淘汰最久未使用的结果。
    """

    def __init__(self, max_entries: int = 2048) -> None:
        if max_entries < 0:
            raise ValueError("max_entries 不能为负数")
        self.max_entries = max_entries
        self._entries: OrderedDict[_Key, AnalysisResult] = OrderedDict()
        self._inflight: dict[_SearchKey, _PendingSearch] = {}

    async def get_or_analyse(
        self,
        engine: UciEngine,
        initial_fen: str,
        moves: Sequence[str] = (),
        *,
        limit: Limit,
        multipv: int = 1,
        searchmoves: Sequence[str] = (),
    ) -> AnalysisResult:
        """复用已完成搜索，同一引擎的相同有限请求同时到达时只搜索一次。

        仅共享纯时间预算；节点、深度、混合限制与限定着法搜索均直接交给引擎。
        正在搜索的任务只在同一引擎实例内合并，不假定不同实例的权重和选项一致。
        单个等待者取消不会中断其他等待者；最后一个离开时取消搜索并等待引擎排空。
        """
        if (
            limit.movetime_ms is None
            or limit.depth is not None
            or limit.nodes is not None
            or searchmoves
        ):
            if searchmoves:
                return await engine.analyse(
                    initial_fen, moves, limit=limit, multipv=multipv, searchmoves=searchmoves
                )
            return await engine.analyse(initial_fen, moves, limit=limit, multipv=multipv)
        if limit.movetime_ms <= 0 or multipv < 1:
            raise ValueError("搜索时间和 MultiPV 必须为正数")
        history = tuple(moves)
        cached = self.get(initial_fen, history, multipv, limit.movetime_ms)
        if cached is not None:
            return cached
        key = (id(engine), initial_fen, history, multipv, limit.movetime_ms)
        pending = self._inflight.get(key)
        if pending is None:
            async def search() -> AnalysisResult:
                result = await engine.analyse(
                    initial_fen, history, limit=limit, multipv=multipv
                )
                self.put(initial_fen, history, multipv, limit.movetime_ms, result)
                return result

            pending = _PendingSearch(asyncio.create_task(search()))
            self._inflight[key] = pending

            def complete(task: asyncio.Task[AnalysisResult]) -> None:
                if self._inflight.get(key) is pending:
                    del self._inflight[key]
                # 即使最后一个等待者已取消，异常也不能成为未读取的后台任务异常。
                if not task.cancelled():
                    task.exception()

            pending.task.add_done_callback(complete)
        pending.waiters += 1
        try:
            return deepcopy(await asyncio.shield(pending.task))
        finally:
            pending.waiters -= 1
            if pending.waiters == 0 and not pending.task.done():
                if self._inflight.get(key) is pending:
                    del self._inflight[key]
                pending.task.cancel()
                # 第二次取消等待者也不能跳过引擎的 stop/bestmove 清理。
                while not pending.task.done():
                    try:
                        await asyncio.shield(pending.task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break

    def get(
        self,
        initial_fen: str,
        moves: Sequence[str],
        multipv: int,
        movetime_ms: int | None,
    ) -> AnalysisResult | None:
        """返回满足搜索条件的独立快照；None 预算取可用的最深预填结果。"""
        history = tuple(moves)
        candidates = []
        for key, result in self._entries.items():
            fen, cached_history, cached_multipv, cached_ms = key
            if fen != initial_fen or cached_history != history:
                continue
            if movetime_ms is not None and (cached_ms is None or cached_ms < movetime_ms):
                continue
            if cached_multipv < multipv and cached_multipv < _legal_move_count(
                initial_fen, history
            ):
                continue
            candidates.append((key, result))
        if not candidates:
            return None
        key, result = max(candidates, key=lambda item: _quality(item[1], multipv))
        self._entries.move_to_end(key)
        snapshot = deepcopy(result)
        snapshot.lines = snapshot.lines[:multipv]
        return snapshot

    def put(
        self,
        initial_fen: str,
        moves: Sequence[str],
        multipv: int,
        movetime_ms: int | None,
        result: AnalysisResult,
    ) -> None:
        """保存可靠的候选，防止浅层重搜覆盖先前的深分析。"""
        if self.max_entries == 0 or not result.lines:
            return
        first = result.lines[0]
        if first.multipv != 1 or not _exact_score(first):
            return
        if movetime_ms is not None and result.bestmove != first.move:
            return  # 尚未完成或 bestmove 与已确认评分不一致，不能复用成完整搜索
        # 请求 MultiPV 3 并不保证引擎最后返回完整的三条：停止时可能还在逐条更新，
        # 去重后也可能出现 1、3 的缺口。只登记实际连续的精确候选覆盖范围；
        # 深层的一条候选不应覆盖此前较浅但完整的三条候选。
        lines = []
        seen = set()
        for rank, line in enumerate(result.lines[:multipv], start=1):
            if line.multipv != rank or not _exact_score(line) or line.move in seen:
                break
            lines.append(line)
            seen.add(line.move)
        if not lines:
            return
        snapshot = deepcopy(result)
        snapshot.lines = deepcopy(lines)
        coverage = len(lines)
        key = (initial_fen, tuple(moves), coverage, movetime_ms)
        previous = self._entries.get(key)
        if previous is None or _quality(snapshot, coverage) >= _quality(previous, coverage):
            self._entries[key] = snapshot
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


def _exact_score(line: InfoLine) -> bool:
    """候选必须有 PV 和精确评分；缺分数不能默认为均势写入共享缓存。"""
    if not line.pv or line.bound is not None:
        return False
    if line.wdl is not None and (
        len(line.wdl) != 3
        or any(type(value) is not int or value < 0 for value in line.wdl)
        or sum(line.wdl) != 1000
    ):
        return False
    return line.wdl is not None or type(line.score_cp) is int or type(line.mate) is int


def _quality(result: AnalysisResult, multipv: int) -> tuple[int, int, int, int]:
    """最佳着法优先；同深度时选候选更完整、搜索节点更多的快照。"""
    lines = result.lines[:multipv]
    return (
        lines[0].depth,
        len(lines),
        min(line.depth for line in lines),
        max((line.nodes or 0) for line in lines),
    )


@lru_cache(maxsize=2048)
def _legal_move_count(initial_fen: str, moves: tuple[str, ...]) -> int:
    """只在候选不足请求数量时核对是否已覆盖全部应着，避免对正常命中反复回放。"""
    position = Position.from_fen(initial_fen, validate=False)
    for text in moves:
        position.push(parse_iccs(text))
    return len(position.legal_moves())
