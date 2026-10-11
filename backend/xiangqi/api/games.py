"""对局：新建、走子、悔棋、AI 走棋、提示。对局只保存在内存里，重启后清空。"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request

from ..core import (
    BLACK,
    RED,
    FenError,
    NotationError,
    Position,
    RuleConfig,
    game_result,
    move_to_chinese,
    move_to_iccs,
    parse_iccs,
    parse_move,
)
from ..core.fen import piece_to_letter
from ..core.notation import BLACK_NAME, RED_NAME
from ..core.variation import pv_to_chinese
from ..engine import (
    AnalysisResult,
    EngineError,
    EngineService,
    EngineUnavailable,
    Limit,
    UciEngine,
    choose_move,
    get_level,
)
from ..library import Library, my_game_headers
from ..openings import TRAP_LINES, match_move, move_note, trap_advice
from ..openings.index import match_position
from ..training import PositionEval, direction_hint, purpose_of_move
from .schemas import (
    GameView,
    HintRequest,
    HintView,
    MoveRecord,
    MoveRequest,
    NewGameRequest,
    PositionView,
    ResultView,
    SavedGame,
    TrapAlertView,
    TrapNoteView,
)

logger = logging.getLogger(__name__)


class GameError(Exception):
    """对局状态不允许这个操作（如棋局已结束、不是你的回合），message 直接显示给用户。"""


@dataclass
class Game:
    id: str
    initial_fen: str
    position: Position
    rules: RuleConfig
    mode: str = "free"  # free | vs_ai
    ai_side: int | None = None  # 人机对战时 AI 执哪一方
    ai_level: int | None = None
    records: list[MoveRecord] = field(default_factory=list)
    hints_used: int = 0
    library_id: int | None = None  # 保存到棋谱库后的 id
    saved: tuple[tuple[str, ...], str] | None = None  # 上次保存的（着法, 结果），避免重复保存
    rng: random.Random = field(default_factory=random.Random)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # 同一对局的操作依次执行
    # 飞刀剧本：AI 先按 book 走（走完或人一变招就转引擎自由应变）
    book: tuple[str, ...] = ()
    book_name: str | None = None
    # 提示的分析结果，按「完整着法历史」缓存：同一局面、同一历史才复用
    _hint_cache: tuple[tuple[str, ...], AnalysisResult] | None = None
    # 3 级提示用：对方最佳应着的分析（键：历史 + 推荐着法）
    _hint_reply_cache: dict[tuple[tuple[str, ...], str], AnalysisResult] = field(
        default_factory=dict
    )
    # 边下边分析：局面评估（键为走到这个局面的全部着法）和讲解（键为到这步为止的全部着法）。
    # 按着法历史作键，悔棋后走了别的着法也不会用错
    position_evals: dict[tuple[str, ...], PositionEval] = field(default_factory=dict)
    # 「走之后」局面的轻量评估（MultiPV 1，只取最佳应着）：做下一轮的「走之前」评估时，
    # 若你走的就是这个最佳应着则直接复用（同一搜索，评好棋）；走了别的着法则按 MultiPV 3 重搜
    reply_evals: dict[tuple[str, ...], PositionEval] = field(default_factory=dict)
    move_explanations: dict[tuple[str, ...], dict] = field(default_factory=dict)

    @classmethod
    def create(cls, body: NewGameRequest, rules: RuleConfig) -> Game:
        """新对局。body.moves 是从起始局面先走的着法（从棋谱某一步开始时保留历史）；
        指定 trap_id 时由飞刀剧本决定开局（body.moves、body.fen 都被忽略——剧本从标准开局起）。"""
        if body.trap_id:
            position = Position.start()
        else:
            position = Position.from_fen(body.fen) if body.fen else Position.start()
        game = cls(uuid.uuid4().hex[:12], position.fen(), position, rules, mode=body.mode)
        if body.trap_id:
            if body.trap_id == "random":
                line = game.rng.choice(list(TRAP_LINES.values()))
            else:
                line = TRAP_LINES.get(body.trap_id)
                if line is None:
                    raise GameError(f"飞刀 {body.trap_id!r} 不存在（可用 /api/traps 查询）")
            if body.mode == "vs_ai":
                ai_plays = body.ai_plays or "trap"
                game.ai_side = line.trap.side if ai_plays == "trap" else -line.trap.side
                game.ai_level = body.ai_level
                game.book = line.moves  # AI 按剧本走，人不跟剧本则转引擎
                game.book_name = line.trap.name
            else:
                # 自由对弈：预摆到陷阱着法前一步（执刀方正面临走不走弃子那步的关键局面）
                for text in line.moves[: line.trap.trap_ply - 1]:
                    move = parse_iccs(text)
                    game.records.append(
                        MoveRecord(
                            iccs=move_to_iccs(move),
                            cn=move_to_chinese(game.position.board, move),
                        )
                    )
                    game.position.push(move)
                game.book_name = line.trap.name
            return game
        for ply, text in enumerate(body.moves, start=1):
            try:
                move = parse_iccs(text)
            except NotationError as e:
                raise GameError(f"第 {ply} 步 {text} 不是 ICCS 着法") from e
            if not position.is_legal(move):
                raise GameError(f"第 {ply} 步 {text} 不合法")
            game.records.append(
                MoveRecord(iccs=move_to_iccs(move), cn=move_to_chinese(position.board, move))
            )
            position.push(move)
        if body.mode == "vs_ai":
            game.ai_side = BLACK if body.user_side == "red" else RED
            game.ai_level = body.ai_level
        return game

    @property
    def move_list(self) -> list[str]:
        return [r.iccs for r in self.records]

    def result(self):
        return game_result(self.position, self.rules)

    @property
    def ai_to_move(self) -> bool:
        return self.mode == "vs_ai" and self.position.turn == self.ai_side and self.result() is None

    def play(self, text: str) -> None:
        """玩家走一步。"""
        if self.ai_to_move:
            raise GameError("现在轮到 AI 走棋")
        expected = (
            self.book[len(self.records)]
            if self.book and len(self.records) < len(self.book)
            else None
        )
        self._apply(text, by_ai=False)
        if expected is not None and self.records[-1].iccs != expected:
            self.book = ()  # 没按剧本走：AI 不再照本宣科，转引擎自由应变

    def play_ai(self, iccs: str) -> None:
        self._apply(iccs, by_ai=True)

    def _apply(self, text: str, *, by_ai: bool) -> None:
        if self.result() is not None:
            raise GameError("棋局已经结束")
        pos = self.position
        move = parse_move(pos.board, pos.turn, text)
        record = MoveRecord(
            iccs=move_to_iccs(move), cn=move_to_chinese(pos.board, move), by_ai=by_ai
        )
        pos.push(move)
        self.records.append(record)

    def undo(self) -> None:
        """悔棋。人机对战时撤回到轮到你走，并且至少撤掉你这一方的一步。
        按轮到哪一方判断，而不是看着法是不是 AI 走的：从棋谱某一步开始的对局，
        之前的着法双方都不是 AI 走的。"""
        count = 1
        if self.mode == "vs_ai" and self.position.turn != self.ai_side:
            count = 2  # 轮到你走：撤掉对方的上一步和你的上一步
        if count > len(self.records):
            raise GameError("没有可以悔的棋")
        for _ in range(count):
            self.position.pop()
            self.records.pop()

    def view(self) -> GameView:
        pos = self.position
        result = self.result()
        result_view = None
        if result is not None:
            winner = None if result.winner is None else _side_name(result.winner)
            result_view = ResultView(winner=winner, reason=result.reason, text=result.text)
        return GameView(
            id=self.id,
            initial_fen=self.initial_fen,
            mode=self.mode,  # type: ignore[arg-type]
            user_side=None if self.ai_side is None else _side_name(-self.ai_side),
            ai_level=self.ai_level,
            ai_to_move=self.ai_to_move,
            hints_used=self.hints_used,
            library_id=self.library_id,
            book_name=self.book_name,
            moves=self.records,
            position=PositionView(
                fen=pos.fen(),
                turn=_side_name(pos.turn),
                board=[piece_to_letter(p) if p else "." for p in pos.board],
                legal_moves=[] if result else [move_to_iccs(m) for m in pos.legal_moves()],
                last_move=self.records[-1].iccs if self.records else None,
                in_check=pos.in_check(),
                result=result_view,
            ),
        )


def _result_code(game: Game) -> str:
    result = game.result()
    if result is None:
        return "*"
    return {RED: "1-0", BLACK: "0-1", None: "1/2-1/2"}[result.winner]


def save_to_library(game: Game, library: Library) -> int:
    """把对局保存（或更新）到棋谱库，返回库中的 id。会访问数据库，在线程中调用。"""
    code = _result_code(game)
    user_side = None if game.ai_side is None else _side_name(-game.ai_side)
    headers = my_game_headers(mode=game.mode, ai_level=game.ai_level, user_side=user_side)
    game.library_id = library.save_game(
        initial_fen=game.initial_fen,
        moves=game.move_list,
        result=code,
        headers=headers,
        library_id=game.library_id,
    )
    game.saved = (tuple(game.move_list), code)
    return game.library_id


async def _autosave(request: Request, game: Game) -> None:
    """棋局结束时自动保存到棋谱库（悔棋后再下完会更新同一条记录）。"""
    if game.result() is None or game.saved == (tuple(game.move_list), _result_code(game)):
        return
    try:
        # 数据库可能正被导入占用写锁，放到线程里等，不阻塞其他请求
        await asyncio.to_thread(save_to_library, game, request.app.state.library)
    except sqlite3.Error:
        logger.exception("自动保存对局失败")


def _side_name(side: int) -> str:
    return "red" if side == RED else "black"


def red_expected(score: float, turn: int) -> float:
    """走棋方视角的期望得分 → 红方视角。"""
    return score if turn == RED else 1.0 - score


async def run_engine(coro_factory):
    """调用引擎，把不可用 / 出错 / 超时转换成 503，信息直接给用户看。"""
    try:
        return await coro_factory()
    except (EngineUnavailable, EngineError) as e:
        raise HTTPException(503, str(e)) from e
    except TimeoutError as e:
        raise HTTPException(503, "引擎响应超时，请稍后再试") from e


router = APIRouter(prefix="/api/games", tags=["对局"])


def _store(request: Request) -> dict[str, Game]:
    return request.app.state.games


def _engines(request: Request) -> EngineService:
    return request.app.state.engines


def _get(request: Request, game_id: str) -> Game:
    game = _store(request).get(game_id)
    if game is None:
        raise HTTPException(404, "对局不存在（服务重启后内存中的对局会清空）")
    return game


@router.post("", response_model=GameView)
async def new_game(body: NewGameRequest, request: Request) -> GameView:
    try:
        game = Game.create(body, request.app.state.config.rules)
    except FenError as e:
        raise HTTPException(400, f"FEN 无效：{e}") from e
    except GameError as e:
        raise HTTPException(400, str(e)) from e
    _store(request)[game.id] = game
    return game.view()


@router.get("/{game_id}", response_model=GameView)
async def get_game(game_id: str, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:  # 等正在进行的 AI 走棋完成，避免返回走到一半的状态
        return game.view()


@router.post("/{game_id}/moves", response_model=GameView)
async def play_move(game_id: str, body: MoveRequest, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:
        try:
            game.play(body.move)
        except (NotationError, GameError) as e:
            raise HTTPException(400, str(e)) from e
        await _autosave(request, game)
        return game.view()


@router.post("/{game_id}/undo", response_model=GameView)
async def undo_move(game_id: str, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:
        try:
            game.undo()
        except GameError as e:
            raise HTTPException(400, str(e)) from e
        return game.view()


@router.post("/{game_id}/save", response_model=SavedGame)
async def save_game(game_id: str, request: Request) -> SavedGame:
    """手动把（未下完的）对局保存到棋谱库；再次保存会更新同一条记录。"""
    game = _get(request, game_id)
    async with game.lock:
        try:
            library_id = await asyncio.to_thread(save_to_library, game, request.app.state.library)
        except sqlite3.Error as e:
            raise HTTPException(503, f"保存失败（棋谱库正忙或无法写入）：{e}") from e
        return SavedGame(library_id=library_id)


@router.post("/{game_id}/ai-move", response_model=GameView)
async def ai_move(game_id: str, request: Request) -> GameView:
    """人机对战中让 AI 走一步（按对局的难度级别）。飞刀局先按剧本走，剧本走完或人变招后转引擎。"""
    game = _get(request, game_id)
    async with game.lock:
        if not game.ai_to_move:
            raise HTTPException(400, "现在不是 AI 走棋")
        if game.book and len(game.move_list) < len(game.book):
            scripted = game.book[len(game.move_list)]
            if _is_legal_text(game.position, scripted):
                game.play_ai(scripted)  # 按飞刀剧本走，不调用引擎
                await _autosave(request, game)
                return game.view()
            game.book = ()  # 剧本与当前局面不符（防御性处理），转引擎
        level = get_level(game.ai_level or 1)
        engine = await run_engine(_engines(request).player)
        result = await run_engine(
            lambda: engine.analyse(
                game.initial_fen, game.move_list, limit=level.limit(), multipv=level.multipv
            )
        )
        move = choose_move(result, level, game.rng)
        if move is None:
            raise HTTPException(500, "引擎没有给出着法")
        try:
            game.play_ai(move)
        except NotationError as e:
            raise HTTPException(
                503, f"引擎给出了不合法的着法 {move}，请检查 config.toml 中 [engine] 的 flavor"
            ) from e
        await _autosave(request, game)
        return game.view()


async def _hint_analysis(
    request: Request, game: Game, engine: UciEngine | None = None
) -> AnalysisResult:
    """提示用的引擎分析，按完整着法历史缓存（同一局面先要 1 级、再要 3 级时不重复计算）。
    不传 engine 时按需启动对局引擎。引擎不可用时抛 EngineUnavailable / EngineError；
    给不出合法着法时抛 EngineError。同样的局面还会命中全局分析缓存（跨对局复用）。"""
    history = tuple(game.move_list)
    if game._hint_cache is not None and game._hint_cache[0] == history:
        return game._hint_cache[1]
    engines = _engines(request)
    movetime = engines.config.hint_movetime_ms if engines.config else 1500
    cache = request.app.state.analysis_cache
    analysis = cache.get(game.initial_fen, history, 1, movetime)  # 先查缓存再动引擎
    if analysis is None:
        engine = engine or await engines.player()
        analysis = await engine.analyse(
            game.initial_fen, game.move_list, limit=Limit(movetime_ms=movetime), multipv=1
        )
        cache.put(game.initial_fen, history, 1, movetime, analysis)
    if not analysis.lines or not _is_legal_text(game.position, analysis.lines[0].move):
        game._hint_cache = None
        raise EngineError("引擎没有给出合法的着法，请检查 config.toml 中 [engine] 的 flavor")
    game._hint_cache = (history, analysis)
    return analysis


async def _hint_reply(request: Request, game: Game, move: str) -> AnalysisResult | None:
    """推荐着法走后对方的最佳应着（3 级提示的「为什么」用）。按 (着法历史, 着法) 缓存，
    同样命中全局分析缓存（跨对局复用）。"""
    engines = _engines(request)
    history = tuple(game.move_list)
    key = (history, move)
    cached = game._hint_reply_cache.get(key)
    if cached is not None:
        return cached
    configured_ms = engines.config.hint_movetime_ms if engines.config else 1500
    movetime = max(300, min(configured_ms // 2, 600))
    moves = [*game.move_list, move]
    cache = request.app.state.analysis_cache
    analysis = cache.get(game.initial_fen, moves, 1, movetime)  # 先查缓存再动引擎
    if analysis is None:
        engine = await engines.player()
        analysis = await engine.analyse(
            game.initial_fen, moves, limit=Limit(movetime_ms=movetime)
        )
        cache.put(game.initial_fen, moves, 1, movetime, analysis)
    if not analysis.lines:
        return None
    game._hint_reply_cache[key] = analysis
    return analysis


@router.post("/{game_id}/hint", response_model=HintView)
async def hint(game_id: str, body: HintRequest, request: Request) -> HintView:
    """1 级提示：只指出方向（不泄露着法；没有引擎也能用）；2 级提示：该动哪个子；
    3 级提示：具体着法、主要变化、走完后的胜率，以及为什么这么走（含飞刀标注与告警）。"""
    game = _get(request, game_id)
    async with game.lock:
        if game.result() is not None:
            raise HTTPException(400, "棋局已经结束")
        if game.ai_to_move:
            raise HTTPException(400, "现在轮到 AI 走棋")
        pos = game.position
        if body.level == 1:
            # 方向提示不依赖引擎：只在对局引擎已经在运行时借用它判断「主题」，
            # 不为它启动引擎（引擎有问题时启动可能要等很久）
            best = None
            engine = _engines(request).running("player")
            if engine is not None:
                try:
                    best = (await _hint_analysis(request, game, engine)).lines[0].move
                except (EngineUnavailable, EngineError, TimeoutError):
                    pass  # 引擎出错时只用规则引擎的特征
            game.hints_used += 1
            return HintView(level=1, text=direction_hint(pos, best))
        analysis = await run_engine(lambda: _hint_analysis(request, game))
        game.hints_used += 1
        best = analysis.lines[0]
        if body.level == 2:
            return _hint_view(pos, best, level=2)
        try:
            reply = await run_engine(lambda: _hint_reply(request, game, best.move))
        except HTTPException:
            reply = None  # 应着搜索失败不影响提示本身
        return _hint_view(pos, best, level=3, reply=reply, trap_index=request.app.state.trap_index)


def _is_legal_text(pos: Position, text: str) -> bool:
    try:
        return pos.is_legal(parse_iccs(text))
    except NotationError:
        return False


def _hint_view(
    pos: Position,
    best,
    level: int,
    reply: AnalysisResult | None = None,
    trap_index: dict | None = None,
) -> HintView:
    from_square = best.move[:2]
    piece = pos.board[parse_iccs(best.move)[0]]
    name = (RED_NAME if piece > 0 else BLACK_NAME)[abs(piece)]
    if level == 2:
        return HintView(level=2, from_square=from_square, text=f"提示：想想这个{name}可以怎么走。")
    pv_cn = pv_to_chinese(pos, best.pv) or [move_to_chinese(pos.board, parse_iccs(best.move))]
    red_win = red_expected(best.expected_score(), pos.turn)
    side_win = red_win if pos.turn == RED else 1 - red_win
    text = f"引擎推荐：{pv_cn[0]}。"
    if len(pv_cn) > 1:
        text += f"后续可能：{' '.join(pv_cn[:5])}。"
    text += f"走完后{'红' if pos.turn == RED else '黑'}方期望得分约 {side_win:.0%}。"

    # 为什么这么走：规则引擎事实 + 对方最佳应着 + 子力得失（识别弃子抢攻）
    purpose_text: str | None = None
    if reply is not None and reply.lines:
        r = reply.lines[0]
        score_after = 1.0 - r.expected_score()
        purpose = purpose_of_move(
            pos,
            best.move,
            pv=list(r.pv)[:4],
            score_before=best.expected_score(),
            score_after=score_after,
            deep=True,
        )
        purpose_text = purpose.text
        reply_pos = pos.copy()
        reply_pos.push(parse_iccs(best.move))
        reply_cn = pv_to_chinese(reply_pos, r.pv, 1)
        if reply_cn:
            text += f"对方最好应以{reply_cn[0]}。"
    else:
        purpose = purpose_of_move(pos, best.move, score_before=best.expected_score(), deep=True)
        purpose_text = purpose.text
    if purpose_text:
        text += f"为什么：{purpose_text}"

    # 飞刀标注与告警：按当前局面和对手的着法匹配飞刀剧本；剧本没命中但确实是弃子抢攻
    # （任何局面、任何阶段都可能出现）时，也标成通用飞刀。这两项通过 trap / alert 字段返回，
    # 前端单独高亮显示，不混进 text。
    trap_note, alert = None, None
    if trap_index is not None:
        match = match_move(trap_index, pos, best.move)
        if match is not None:
            trap_note = TrapNoteView(
                id=match.trap.id, name=match.trap.name, note=move_note(match)
            )
        elif purpose.sacrifice:
            trap_note = TrapNoteView(id="sacrifice", name="飞刀", note=f"弃子抢攻：{purpose.text}")
        advice = trap_advice(pos, match_position(trap_index, pos))
        if advice is not None:
            alert = TrapAlertView(
                kind=advice.kind, id=advice.id, name=advice.name, advice=advice.advice
            )

    return HintView(
        level=3,
        from_square=from_square,
        move=best.move,
        cn=pv_cn[0],
        pv_cn=pv_cn,
        red_win=round(red_win, 4),
        purpose=purpose_text,
        trap=trap_note,
        alert=alert,
        text=text,
    )
