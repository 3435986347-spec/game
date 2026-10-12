"""引擎状态与实时分析（评估条）。

实时分析通过 WebSocket /ws/analysis 进行：
- 客户端发送 {"type": "start", "game_id": "...", "multipv": 3} 开始分析该对局的当前局面，
  或 {"type": "start", "fen": "...", "moves": [...], "multipv": 3} 分析任意局面（如打谱时）；
  再次发送 start 会停止上一次分析、开始新的；发送 {"type": "stop"} 停止。
- 服务端推送 {"type": "info", "game_id", "ply", "fen", "depth", "lines": [...]}，
  每条 line 含 move、cn（中文）、pv_cn、red_win（红方期望得分 0..1）、score_cp / mate（红方视角）。
  search 含引擎报告的累计 nodes、elapsed_ms、nps；缓存预填的 search 为 null。
  出错时推送 {"type": "error", "message": "..."}。
分析引擎全局只有一个，新的分析请求（包括其他浏览器标签页的）会打断正在进行的分析。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import aclosing
from dataclasses import dataclass

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..core import RED, FenError, NotationError, Position, parse_iccs
from ..core.variation import pv_to_chinese
from ..engine import (
    LEVELS,
    AnalysisCache,
    AnalysisResult,
    EngineError,
    EngineService,
    EngineUnavailable,
    InfoLine,
)
from ..openings import match_move, move_note, trap_advice
from ..openings.index import match_position
from ..training import purpose_of_move
from .games import Game, red_expected
from .schemas import EngineStatusView, LevelView

router = APIRouter(tags=["引擎"])
logger = logging.getLogger(__name__)

_MIN_INTERVAL = 0.25  # 深度提升后至少隔这么久推送一次
_MAX_STALE = 2.0  # 同一深度内分数细化（窗口重搜）最多隔这么久也推送一次


@router.get("/api/engine", response_model=EngineStatusView)
async def engine_status(request: Request) -> EngineStatusView:
    status = await request.app.state.engines.status()
    levels = [LevelView(level=lv.level, name=lv.name) for lv in LEVELS.values()]
    return EngineStatusView(levels=levels, **status)


@router.websocket("/ws/analysis")
async def analysis_socket(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    task: asyncio.Task[None] | None = None
    try:
        while True:
            message = await ws.receive_json()
            await _cancel(task)
            task = None
            kind = message.get("type") if isinstance(message, dict) else None
            request_id = message.get("request_id") if isinstance(message, dict) else None
            if kind == "start":
                try:
                    target = _target(app, message)
                    multipv = max(1, min(int(message.get("multipv", 3)), 5))
                except (ValueError, TypeError, OverflowError) as e:
                    await _send_error(ws, str(e), request_id)
                    continue
                # 停止旧搜索的握手会让出事件循环；注册下一任务也必须在同一把锁内，
                # 否则多个连接可能都只取消同一旧任务，留下并行的新任务。
                async with app.state.analysis_lock:
                    await _preempt_other(app, ws)
                    task = asyncio.create_task(
                        _stream(
                            ws,
                            app.state.engines,
                            target,
                            multipv,
                            app.state.trap_index,
                            app.state.analysis_cache,
                        )
                    )
                    app.state.analysis_task, app.state.analysis_socket = task, ws
                    app.state.analysis_request_id = request_id
            elif kind == "stop":
                reply = {"type": "stopped"}
                if request_id is not None:
                    reply["request_id"] = request_id
                await ws.send_json(reply)
            else:
                await ws.send_json({"type": "error", "message": f"未知的消息类型：{kind!r}"})
    except WebSocketDisconnect:
        pass
    finally:
        await _cancel(task)


async def _preempt_other(app, ws: WebSocket) -> None:
    """分析引擎只有一个：其他连接（如另一个浏览器标签页）正在分析时，停掉它并告诉它原因。"""
    other_task = app.state.analysis_task
    other_ws = app.state.analysis_socket
    other_request_id = getattr(app.state, "analysis_request_id", None)
    if other_task is None or other_ws is ws or other_task.done():
        return
    await _cancel(other_task)
    await _send_error(
        other_ws, "实时分析已在另一个页面打开，这里暂停了",
        other_request_id,
    )


async def _cancel(task: asyncio.Task[None] | None) -> None:
    """停止分析任务并等它结束（已结束的任务也 await 一次，取走其中的异常）。"""
    if task is None:
        return
    if not task.cancelling():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


@dataclass(frozen=True)
class _Target:
    """要分析的局面：起始局面 + 着法（保留历史，引擎才能判断重复局面）。"""

    game_id: str | None
    initial_fen: str
    moves: list[str]
    position: Position  # 走完 moves 之后的局面
    preferred_trap_id: str | None = None
    request_id: str | None = None


def _target(app, message: dict) -> _Target:
    """start 消息里的分析对象：进行中的对局（game_id），或任意局面（fen + moves）。"""
    request_id = message.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128):
        raise ValueError("request_id 应为不超过128字符的字符串")
    if message.get("game_id") is not None:
        game: Game | None = app.state.games.get(message.get("game_id"))
        if game is None:
            raise ValueError("对局不存在")
        # 记下请求时的局面；之后对局再变化，客户端会发来新的 start
        return _Target(
            game.id, game.initial_fen, game.move_list, game.position.copy(), game._selected_trap_id,
            request_id,
        )
    fen = message.get("fen")
    if not isinstance(fen, str):
        raise ValueError("缺少 game_id 或 fen")
    moves = message.get("moves") or []
    try:
        position = Position.from_fen(fen)
        for text in moves:
            move = parse_iccs(str(text))
            if not position.is_legal(move):
                raise ValueError(f"着法 {text} 不合法")
            position.push(move)
    except (FenError, NotationError) as e:
        raise ValueError(f"局面无效：{e}") from e
    return _Target(
        None, Position.from_fen(fen).fen(), [str(m) for m in moves], position,
        request_id=request_id,
    )


async def _stream(
    ws: WebSocket,
    engines: EngineService,
    target: _Target,
    multipv: int,
    trap_index: dict,
    cache: AnalysisCache,
) -> None:
    initial_fen, moves, position = target.initial_fen, target.moves, target.position
    ply = len(moves)
    if not position.legal_moves():  # 将死或困毙：没有可分析的着法，直接告诉前端
        await ws.send_json(
            await _info_message(
                target.game_id,
                ply,
                position,
                [],
                trap_index,
                seeded=False,
                preferred_trap_id=target.preferred_trap_id,
                status="complete",
                request_id=target.request_id,
            )
        )
        return
    # 这个局面之前分析过（悔棋走回来、反复研究同一个开局）：先把缓存里的更深结果发出去，
    # 面板立刻有内容；实时搜索统计单独更新，候选着法更深后自然接替缓存。
    seeded = cache.get(initial_fen, moves, multipv, None)
    if seeded is not None and seeded.lines and seeded.lines[0].depth >= 8:
        await ws.send_json(
            await _info_message(
                target.game_id,
                ply,
                position,
                seeded.lines,
                trap_index,
                seeded=True,
                preferred_trap_id=target.preferred_trap_id,
                request_id=target.request_id,
            )
        )
    engine = None
    try:
        engine = await engines.analyser()
        loop = asyncio.get_running_loop()
        last_sent = 0.0
        last_depth = -1
        last_lines: list[InfoLine] | None = None
        latest_lines: list[InfoLine] = []
        async with aclosing(engine.analyse_infinite(initial_fen, moves, multipv=multipv)) as gen:
            async for lines in gen:
                if not lines:
                    continue
                latest_lines = list(lines)
                if lines == last_lines:
                    continue  # 引擎重复输出同一快照，不重发相同内容。
                # 最佳着法（multipv 1）一算到更深就推送，不等第 2、3 名凑齐同一深度——
                # 「下一步走法」永远显示引擎已经算到的最新结果；第 2、3 名稍后自己跟上来。
                # 同一深度内的窗口重搜最多每 _MAX_STALE 秒推一次。
                depth = lines[0].depth
                elapsed = loop.time() - last_sent
                if (depth > last_depth and elapsed >= _MIN_INTERVAL) or elapsed >= _MAX_STALE:
                    last_sent = loop.time()
                    if depth > last_depth:
                        last_depth = depth
                    last_lines = list(lines)
                    cache.put(
                        initial_fen,
                        moves,
                        multipv,
                        None,
                        AnalysisResult(lines=list(lines), bestmove=None),
                    )
                    await ws.send_json(
                        await _info_message(
                            target.game_id,
                            ply,
                            position,
                            lines,
                            trap_index,
                            seeded=False,
                            preferred_trap_id=target.preferred_trap_id,
                            request_id=target.request_id,
                        )
                    )
        if latest_lines:
            await ws.send_json(
                await _info_message(
                    target.game_id, ply, position, latest_lines, trap_index,
                    preferred_trap_id=target.preferred_trap_id, status="complete",
                    request_id=target.request_id,
                )
            )
    except (EngineUnavailable, EngineError) as e:
        await _send_error(ws, str(e), target.request_id)
    except WebSocketDisconnect:
        pass
    except Exception:  # 意外错误也要告诉前端，否则界面会一直等待
        logger.exception("实时分析出错")
        await _send_error(ws, "实时分析出错，详见后端日志", target.request_id)
    finally:
        if engine is not None:
            _cache_completed(cache, target, multipv, engine)


def _cache_completed(cache: AnalysisCache, target: _Target, multipv: int, engine) -> None:
    """只复用收到 bestmove 的完成结果，真实 UCI 耗时未知时不估算有限预算。"""
    completed = getattr(engine, "completed_search", None)
    if completed is None or (
        completed.initial_fen != target.initial_fen
        or completed.moves != tuple(target.moves)
        or completed.multipv != multipv
    ):
        return
    result = completed.result
    times = [line.time_ms for line in result.lines]
    if not times or any(value is None or value <= 0 for value in times):
        return
    cache.put(target.initial_fen, target.moves, multipv, min(times), result)


async def _send_error(ws: WebSocket, message: str, request_id: str | None = None) -> None:
    with contextlib.suppress(Exception):
        reply = {"type": "error", "message": message}
        if request_id is not None:
            reply["request_id"] = request_id
        await ws.send_json(reply)


def _line_extras(
    position: Position,
    lines: list[InfoLine],
    trap_index: dict,
    preferred_trap_id: str | None = None,
) -> tuple[list[tuple[str, dict | None]], dict | None]:
    """在后台线程里算：每条候选着法的目的与飞刀标注，以及当前局面的飞刀告警。

    飞刀标注有两类：
    1. 命中飞刀剧本：候选着法走后落在某条飞刀剧本的局面 → 标注剧本名（开局、中局任何步都查）；
    2. 通用飞刀（随机应变）：候选着法按主要变化弃了子、但期望得分不低于最佳着法 →
       标「飞刀：弃子抢攻」并附目的。任何局面都可能出现，不限于开局。
    """
    per_line: list[tuple[str, dict | None]] = []
    best_score = lines[0].expected_score() if lines else None
    for line in lines:
        try:
            purpose = purpose_of_move(
                position,
                line.move,
                pv=list(line.pv)[1:],
                score_before=best_score,
                score_after=line.expected_score(),
                deep=False,
            )
            trap: dict | None = None
            match = match_move(trap_index, position, line.move, preferred_trap_id)
            if match is not None:
                trap = {"id": match.trap.id, "name": match.trap.name, "note": move_note(match)}
            elif purpose.sacrifice:
                trap = {"id": "sacrifice", "name": "飞刀", "note": f"弃子抢攻：{purpose.text}"}
            per_line.append((purpose.text, trap))
        except Exception:  # 防御：单条候选着法异常（引擎着法生僻等）不能中断整个实时分析
            per_line.append(("", None))
    alert = None
    try:
        advice = trap_advice(position, match_position(trap_index, position, preferred_trap_id))
        if advice is not None:
            alert = {
                "kind": advice.kind,
                "id": advice.id,
                "name": advice.name,
                "advice": advice.advice,
            }
    except Exception:  # 同样防御：告警计算失败只影响告警，不影响候选着法
        alert = None
    return per_line, alert


async def _info_message(
    game_id: str | None,
    ply: int,
    position: Position,
    lines: list[InfoLine],
    trap_index: dict,
    seeded: bool = False,
    preferred_trap_id: str | None = None,
    status: str = "searching",
    request_id: str | None = None,
) -> dict:
    sign = 1 if position.turn == RED else -1
    extras, alert = await asyncio.to_thread(
        _line_extras, position, lines, trap_index, preferred_trap_id
    )
    out = []
    for line, (purpose_text, trap) in zip(lines, extras, strict=True):
        pv_cn = pv_to_chinese(position, line.pv, limit=32)
        out.append(
            {
                "move": line.move,
                "cn": pv_cn[0] if pv_cn else line.move,
                "pv_cn": pv_cn,
                "red_win": round(red_expected(line.expected_score(), position.turn), 4),
                "score_cp": None if line.score_cp is None else sign * line.score_cp,
                "mate": None if line.mate is None else sign * line.mate,
                "depth": line.depth,
                "purpose": purpose_text,
                "trap": trap,
            }
        )
    message: dict = {
        "type": "info",
        "game_id": game_id,
        "ply": ply,
        "fen": position.fen(),
        "depth": lines[0].depth if lines else 0,
        "trap_alert": alert,
        "lines": out,
        "status": status,
        "search": None if seeded or not lines else _search_stats(lines),
    }
    if seeded:
        message["seeded"] = True  # 旧结果仅用于候选预填；本次搜索进度来自独立的实时消息。
    if request_id is not None:
        message["request_id"] = request_id
    return message


def _search_stats(lines: list[InfoLine]) -> dict:
    """MultiPV 的 nodes/time 是同一次搜索的累计数，取最大值而非各候选相加。"""
    latest = max(lines, key=lambda line: (line.time_ms or 0, line.nodes or 0))
    nodes = [line.nodes for line in lines if line.nodes is not None]
    times = [line.time_ms for line in lines if line.time_ms is not None]
    return {
        "depth": lines[0].depth,
        "nodes": max(nodes) if nodes else None,
        "elapsed_ms": max(times) if times else None,
        "nps": latest.nps,
    }
