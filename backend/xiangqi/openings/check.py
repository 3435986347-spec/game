"""用真实引擎检查飞刀：弃子成立、主变胜势、破解逐步可以抗衡。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import asdict, dataclass, replace

from ..core import Position, game_result, move_to_iccs, parse_iccs
from ..core.board import CANNON, KNIGHT, ROOK
from ..core.features import PIECE_VALUES
from ..engine import EngineError, EngineService, EngineUnavailable, Limit, UciEngine
from .index import TRAP_LINES, TrapLine

_EPSILON = 1e-9


@dataclass(frozen=True)
class ScoreEvidence:
    """每次评分保留 UCI 输入和原始结果，终局规则评分也明确注明。"""

    label: str
    initial_fen: str
    moves: tuple[str, ...]
    fen: str
    side: int
    searchmoves: tuple[str, ...]
    limit: Limit
    score: float
    depth: int | None = None
    nodes: int | None = None
    score_cp: int | None = None
    mate: int | None = None
    wdl: tuple[int, int, int] | None = None
    pv: tuple[str, ...] = ()
    terminal_reason: str | None = None


@dataclass(frozen=True)
class DefenseCheck:
    ply: int
    move: str
    best_score: float
    score: float
    drop: float


@dataclass(frozen=True)
class TrapCheck:
    # 原有字段及顺序保留；引擎/数据错误时尚未得到的分数为 None，不伪装成零分。
    id: str
    name: str
    passed: bool
    sacrifice_score: float | None
    sacrifice_drop: float | None
    main_score: float | None
    refute_entry_score: float | None
    refute_score: float | None
    errors: tuple[str, ...]
    accepted_score: float | None = None
    defense_checks: tuple[DefenseCheck, ...] = ()
    evidence: tuple[ScoreEvidence, ...] = ()
    failure_kind: str | None = None


@dataclass(frozen=True)
class TrapVerificationRound:
    movetime_ms: int
    check: TrapCheck


@dataclass(frozen=True)
class TrapVerification:
    id: str
    name: str
    status: str  # passed / rejected / pending
    rounds: tuple[TrapVerificationRound, ...]
    reason: str

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def final(self) -> TrapCheck:
        return self.rounds[-1].check


def _material(position: Position, side: int) -> float:
    # 仅比较实际吃掉的子力。兵过河不算吃子，不能因此掩盖或伪造弃子损失。
    return sum(
        PIECE_VALUES.get(abs(piece), 0.0) * (1 if piece * side > 0 else -1)
        for piece in position.board
        if piece
    )


def _replay(line: TrapLine) -> tuple[list[Position], list[Position]]:
    """重放所有着法并核对缓存 FEN，保留规则和 UCI 需要的完整历史。"""
    trap = line.trap
    if trap.side not in (-1, 1) or not 1 <= trap.trap_ply < len(line.moves):
        raise ValueError("执刀方或陷阱半回合数无效")
    if len(line.fens) != len(line.moves) + 1 or len(line.steps) != len(line.moves):
        raise ValueError("主变局面/着法数据不完整")
    if not line.refute or len(line.refute_steps) != len(line.refute):
        raise ValueError("破解变数据不完整")
    position = Position.from_fen(line.fens[0])
    main = [position.copy()]
    for index, text in enumerate(line.moves, start=1):
        if game_result(position) is not None:
            raise ValueError(f"主变第 {index} 步出现在棋局已结束之后")
        position.push(parse_iccs(text))
        step = line.steps[index - 1]
        if (
            position.fen() != line.fens[index]
            or step.fen != position.fen()
            or step.iccs != text
            or step.ply != index
        ):
            raise ValueError(f"主变第 {index} 步与缓存棋谱不一致")
        main.append(position.copy())
    if main[trap.trap_ply - 1].turn != trap.side:
        raise ValueError("陷阱着法不是执刀方的回合")
    position = main[trap.trap_ply].copy()
    if line.refute[0] == line.moves[trap.trap_ply]:
        raise ValueError("破解首步与接受弃子的应着相同")
    refute = [position.copy()]
    for index, text in enumerate(line.refute):
        ply = trap.trap_ply + index + 1
        if game_result(position) is not None:
            raise ValueError(f"破解变第 {ply} 步出现在棋局已结束之后")
        position.push(parse_iccs(text))
        step = line.refute_steps[index]
        if step.fen != position.fen() or step.iccs != text or step.ply != ply:
            raise ValueError(f"破解变第 {ply} 步与缓存棋谱不一致")
        refute.append(position.copy())
    return main, refute


async def _score(
    engine: UciEngine,
    position: Position,
    side: int,
    limit: Limit,
    searchmoves: tuple[str, ...] = (),
    *,
    initial_fen: str,
    label: str,
    evidence: list[ScoreEvidence],
) -> float:
    for text in searchmoves:
        if not position.is_legal(parse_iccs(text)):
            raise ValueError(f"{label} 的限定着法不合法：{text}")
    moves = tuple(move_to_iccs(move) for move in position.moves)
    result = game_result(position)
    if result is not None:
        if searchmoves:
            raise ValueError(f"{label} 试图在终局后分析限定着法")
        score = 0.5 if result.winner is None else float(result.winner == side)
        evidence.append(
            ScoreEvidence(
                label, initial_fen, moves, position.fen(), side, searchmoves, limit,
                score, terminal_reason=result.reason,
            )
        )
        return score
    try:
        analysis = await engine.analyse(
            initial_fen, moves=moves, limit=limit, multipv=1, searchmoves=searchmoves,
        )
    except Exception as error:
        raise EngineError(f"引擎搜索失败：{error}") from error
    if not analysis.lines or not analysis.lines[0].pv:
        raise EngineError("引擎没有给出可用于验证飞刀的分析")
    best = analysis.lines[0]
    try:
        legal = position.is_legal(parse_iccs(best.move))
    except (TypeError, ValueError):
        legal = False
    if not legal or analysis.bestmove != best.move:
        raise EngineError("引擎给出的最佳着法不合法或与分析首着不一致")
    if searchmoves and best.move not in searchmoves:
        raise EngineError("引擎未遵守限定着法，不能验证该着法")
    if best.bound:
        raise EngineError("引擎只给出了分数边界，不能据此确认飞刀")
    if best.wdl is not None:
        if (
            len(best.wdl) != 3
            or any(not isinstance(value, int) or not 0 <= value <= 1000 for value in best.wdl)
            or sum(best.wdl) != 1000
        ):
            raise EngineError("引擎返回的 WDL 分数无效")
    elif best.mate is None and best.score_cp is None:
        raise EngineError("引擎未返回有效评分")
    if any(
        value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value))
        for value in (best.score_cp, best.mate)
    ):
        raise EngineError("引擎返回的数值评分无效")
    expected = best.expected_score()
    if not math.isfinite(expected) or not 0.0 <= expected <= 1.0:
        raise EngineError("引擎返回的期望得分无效")
    score = expected if position.turn == side else 1.0 - expected
    evidence.append(
        ScoreEvidence(
            label, initial_fen, moves, position.fen(), side, searchmoves, limit,
            score, best.depth, best.nodes, best.score_cp, best.mate, best.wdl, tuple(best.pv),
        )
    )
    return score


async def check_trap(engine: UciEngine, line: TrapLine, limit: Limit) -> TrapCheck:
    """有限搜索验证；非法数据或引擎出错均不能通过，已取得的证据仍然保留。"""
    trap = line.trap
    errors: list[str] = []
    evidence: list[ScoreEvidence] = []
    defenses: list[DefenseCheck] = []
    sacrifice = drop = main_score = refute_entry = refute_score = accepted_score = None
    failure_kind = None
    try:
        main, refute = _replay(line)
        before, after, accepted = main[trap.trap_ply - 1 : trap.trap_ply + 2]
        greedy = parse_iccs(line.moves[trap.trap_ply])
        offered = after.board[greedy[1]]
        if (
            offered * trap.side <= 0
            or abs(offered) not in (KNIGHT, CANNON, ROOK)
            or _material(accepted, trap.side) >= _material(before, trap.side)
        ):
            raise ValueError("主变的陷阱应着没有接受执刀方的马、炮或车净弃子")
        initial_fen = line.fens[0]

        async def score(position, side, label, searchmoves=()):
            return await _score(
                engine, position, side, limit, searchmoves,
                initial_fen=initial_fen, label=label, evidence=evidence,
            )

        best_before = await score(before, trap.side, "弃子前最佳")
        sacrifice = await score(
            before, trap.side, "限定弃子着法", (line.moves[trap.trap_ply - 1],),
        )
        drop = max(0.0, best_before - sacrifice)
        if sacrifice < 0.45 - _EPSILON:
            errors.append("弃子后执刀方期望得分低于 0.45")
        if drop > 0.10 + _EPSILON:
            errors.append("弃子相对最佳着法的期望得分下降超过 0.10")
        accepted_score = await score(accepted, trap.side, "接受弃子后")
        main_score = await score(main[-1], trap.side, "主变终点")
        if main_score < 0.75 - _EPSILON:
            errors.append("主变终点执刀方期望得分低于 0.75")
        for index, text in enumerate(line.refute):
            position = refute[index]
            if position.turn == trap.side:
                continue
            ply = trap.trap_ply + index + 1
            best = await score(position, -trap.side, f"破解第 {ply} 步最佳")
            chosen = await score(position, -trap.side, f"破解第 {ply} 步限定", (text,))
            defense_drop = max(0.0, best - chosen)
            defenses.append(DefenseCheck(ply, text, best, chosen, defense_drop))
            if index == 0:
                refute_entry = chosen
                if chosen < 0.45 - _EPSILON or defense_drop > 0.05 + _EPSILON:
                    errors.append("破解第一步无法抗衡，或比最佳防守下降超过 0.05")
            elif defense_drop > 0.05 + _EPSILON:
                errors.append(f"破解第 {ply} 步比同局面最佳防守下降超过 0.05")
        refute_score = await score(refute[-1], -trap.side, "破解终点")
        if refute_score < 0.45 - _EPSILON:
            errors.append("破解变终点受害方期望得分低于 0.45")
    except (EngineError, EngineUnavailable, OSError, TimeoutError, OverflowError) as error:
        failure_kind = "engine"
        errors.append(f"引擎验证未完成：{error}")
    except (TypeError, ValueError, IndexError) as error:
        failure_kind = "data"
        errors.append(f"棋谱验证未完成：{error}")
    return TrapCheck(
        trap.id, trap.name, not errors, sacrifice, drop, main_score,
        refute_entry, refute_score, tuple(errors), accepted_score,
        tuple(defenses), tuple(evidence), failure_kind,
    )


def _margins(check: TrapCheck) -> tuple[float, ...]:
    """正值达标、负值未达标；保留未舍入分数用于阈值和稳定性判断。"""
    values = (
        (check.sacrifice_score, 0.45, 1),
        (check.sacrifice_drop, 0.10, -1),
        (check.main_score, 0.75, 1),
        (check.refute_entry_score, 0.45, 1),
        (check.refute_score, 0.45, 1),
    )
    margins = [sign * (value - threshold) for value, threshold, sign in values if value is not None]
    margins.extend(0.05 - defense.drop for defense in check.defense_checks)
    return tuple(margins)


def _conclusion(check: TrapCheck) -> tuple:
    gates = tuple(margin >= -_EPSILON for margin in _margins(check))
    return check.passed, check.failure_kind, gates


async def verify_trap(engine: UciEngine, line: TrapLine) -> TrapVerification:
    """5 秒初筛、15 秒定稿；近门槛或结论变化时用 30 秒复核。"""
    rounds = []
    for duration in (5000, 15000):
        check = await check_trap(engine, line, Limit(movetime_ms=duration))
        rounds.append(TrapVerificationRound(duration, check))
    needs_final = (
        _conclusion(rounds[0].check) != _conclusion(rounds[1].check)
        or any(abs(margin) <= 0.02 + _EPSILON for item in rounds for margin in _margins(item.check))
    )
    if needs_final:
        check = await check_trap(engine, line, Limit(movetime_ms=30000))
        rounds.append(TrapVerificationRound(30000, check))
    final = rounds[-1].check
    if any(item.check.failure_kind == "engine" for item in rounds):
        status, reason = "pending", "引擎出错，已有证据不足以完成验证"
    elif len(rounds) == 3 and _conclusion(rounds[-2].check) != _conclusion(final):
        status, reason = "pending", "15 秒与 30 秒的门槛结论仍不一致"
    elif final.passed:
        status, reason = "passed", "已通过逐步验证及有限搜索稳定性复核"
    else:
        status, reason = "rejected", "棋谱或评分未达到收录门槛"
    return TrapVerification(line.trap.id, line.trap.name, status, tuple(rounds), reason)


async def _run(
    config_path: str | None, ids: list[str] | None, movetime_ms: int, verify: bool = False,
) -> tuple[str, list]:
    from ..config import load_config

    selected = list(TRAP_LINES.values())
    if ids:
        missing = set(ids) - TRAP_LINES.keys()
        if missing:
            raise ValueError(f"不存在的飞刀 id：{', '.join(sorted(missing))}")
        selected = [TRAP_LINES[trap_id] for trap_id in dict.fromkeys(ids)]
    config = load_config(config_path).engine
    if verify and config is not None:
        config = replace(config, threads=4, hash_mb=256)
    service = EngineService(config)
    try:
        engine = await service.reviewer()
        results = []
        for line in selected:
            result = (
                await verify_trap(engine, line) if verify
                else await check_trap(engine, line, Limit(movetime_ms=movetime_ms))
            )
            results.append(result)
        return engine.name or "象棋引擎", results
    finally:
        await service.close()


def _display_score(value: float | None) -> str:
    return "未完成" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xiangqi-check-traps", description=__doc__)
    parser.add_argument("--config", help="config.toml 路径（默认按应用的查找规则）")
    parser.add_argument("--trap", action="append", help="只检查指定 id，可以重复指定")
    parser.add_argument("--movetime-ms", type=int, default=5000, help="每次搜索毫秒数，默认 5000")
    parser.add_argument(
        "--verify", action="store_true", help="4 线程/256 MiB；5/15/必要时 30 秒复核",
    )
    parser.add_argument("--json", action="store_true", help="输出可保存的 JSON 检查证据")
    args = parser.parse_args(argv)
    if args.movetime_ms <= 0:
        parser.error("--movetime-ms 必须大于 0")
    try:
        name, results = asyncio.run(_run(args.config, args.trap, args.movetime_ms, args.verify))
    except (EngineError, EngineUnavailable, OSError, TimeoutError, ValueError) as error:
        sys.exit(f"飞刀检查无法完成：{error}")
    if args.json:
        payload = {
            "engine": name,
            "movetime_ms": args.movetime_ms,
            "results": [asdict(result) for result in results],
        }
        if args.verify:
            payload.update(verify=True, threads=4, hash_mb=256, movetime_ms=[5000, 15000, 30000])
        print(json.dumps(payload, ensure_ascii=False))
    else:
        timing = "5/15/必要时 30 秒复核" if args.verify else f"每次有限搜索 {args.movetime_ms} ms"
        print(f"引擎：{name}；{timing}，结果随深度可能变化。")
        for result in results:
            report = result.final if args.verify else result
            status = {"passed": "通过", "rejected": "否决", "pending": "待验证"}.get(
                result.status if args.verify else "passed" if result.passed else "rejected",
            )
            print(
                f"{status} {report.name}："
                f"弃子 {_display_score(report.sacrifice_score)}"
                f"（下降 {_display_score(report.sacrifice_drop)}），"
                f"接受后 {_display_score(report.accepted_score)}，"
                f"主变 {_display_score(report.main_score)}，"
                f"破解首步 {_display_score(report.refute_entry_score)}，"
                f"破解终点 {_display_score(report.refute_score)}"
            )
            for error in report.errors:
                print(f"  {error}")
            if args.verify:
                print(f"  {result.reason}")
    if not all(result.passed for result in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
