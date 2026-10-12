#!/usr/bin/env python3
"""复核已保存公开棋谱；候选和实时证据独立于正式飞刀库。

从仓库根目录运行：backend/.venv/bin/python backend/scripts/verify_trap_candidates.py
可用 --candidate 限定某条，--force 重新搜索；默认复用来源 hash 相同的已完成报告。
只用真实配置引擎、4 线程、256 MiB Hash，逐一搜索，不并发争用 CPU。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from xiangqi.config import load_config  # noqa: E402
from xiangqi.core import (  # noqa: E402
    Position,
    game_result,
    move_to_chinese,
    parse_iccs,
)
from xiangqi.core.board import BLACK, CANNON, KNIGHT, RED, ROOK  # noqa: E402
from xiangqi.core.features import piece_name  # noqa: E402
from xiangqi.engine import EngineService, Limit  # noqa: E402
from xiangqi.engine.uci import find_network  # noqa: E402
from xiangqi.openings import TRAP_LINES  # noqa: E402
from xiangqi.openings.check import (  # noqa: E402
    ScoreEvidence,
    _material,
    _score,
    verify_trap,
)
from xiangqi.openings.index import TrapMove, replay_trap  # noqa: E402
from xiangqi.openings.traps import Trap  # noqa: E402

DATA = REPO / "data" / "trap-verification" / "2026-10-12"
EPS = 1e-9
POLICY_VERSION = 2


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def candidate_input(candidate, moves, source):
    return fingerprint(
        {
            "candidate": asdict(candidate),
            "html_sha256": source["html_sha256"],
            "moves": list(moves),
            "policy_version": POLICY_VERSION,
            "engine_main": {"source_prefix_plies": 19, "pv_plies": 12, "attempts": 1}
            if candidate.id == "pingfengma-qima-xianche"
            else None,
        }
    )


def baseline_input(line):
    return fingerprint(
        {
            "id": line.trap.id,
            "trap_ply": line.trap.trap_ply,
            "side": line.trap.side,
            "main": line.moves,
            "refute": line.refute,
            "policy_version": POLICY_VERSION,
        }
    )


def accepted_generation_evidence(original):
    for item in original["verification"]["rounds"]:
        if item["movetime_ms"] == 30000:
            for evidence in item["check"]["evidence"]:
                if evidence["label"] == "接受弃子后":
                    return evidence
    raise ValueError("缺少30秒接受弃子后的真实生成证据")


def engine_main_input(candidate, moves, source, original):
    accepted = accepted_generation_evidence(original)
    return fingerprint(
        {
            "source_input": candidate_input(candidate, moves, source),
            "actual_selected_pv": accepted["pv"][:12],
            "accepted_generation_evidence": accepted,
            "actual_refute": original["proposed_trap"]["refute"],
            "refute_generation_evidence": original["refute_generation"],
            "source_report_fingerprint": fingerprint(original),
        }
    )


def cache_valid(report, metadata, input_fingerprint, main_kind):
    keys = (
        "binary_sha256",
        "network_sha256",
        "threads",
        "hash_mb",
        "checker_sha256",
        "script_sha256",
    )
    return (
        report.get("status") in ("passed", "rejected", "pending")
        and report.get("policy_version") == POLICY_VERSION
        and report.get("input_fingerprint") == input_fingerprint
        and report.get("main_kind") == main_kind
        and all(
            key in report.get("engine", {})
            and key in metadata
            and report["engine"][key] == metadata[key]
            for key in keys
        )
    )


def archive_previous(candidate_id, phase=None):
    """重审前保留旧报告和原始日志，避免 --force 或版本变化抹掉已有证据。"""
    paths = [path for path in DATA.glob(f"{candidate_id}.*") if path.suffix in (".json", ".jsonl")]
    if any(path.exists() for path in paths):
        timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S%f")
        target = DATA / "archive" / timestamp
        target.mkdir(parents=True, exist_ok=True)
        for path in paths:
            if path.exists():
                shutil.copy2(path, target / path.name)


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    html: str
    url: str
    category_url: str
    branch: str
    trap_ply: int
    side: int


CANDIDATES = (
    Candidate(
        "pingfengma-qima-xianche",
        "屏风马弃马陷车",
        "pingfengma.html",
        "https://www.xiangqiqipu.com/Category/View-28219.html",
        "https://www.xiangqiqipu.com/Category/View-28219.html",
        "main",
        18,
        BLACK,
    ),
    Candidate(
        "gansipao",
        "敢死炮",
        "gansipao.html",
        "https://www.xiangqiqipu.com/Category/View-4733.html",
        "https://www.xiangqiqipu.com/Category/View-4733.html",
        "move_0_9_2",
        3,
        RED,
    ),
    Candidate(
        "tiehuache",
        "铁滑车",
        "tiehuache.html",
        "https://www.xiangqiqipu.com/Category/View-5771.html",
        "https://www.xiangqiqipu.com/Category/View-32189.html",
        "main",
        1,
        RED,
    ),
    Candidate(
        "shuang-tiehuache",
        "双铁滑车",
        "tiehuache-variations.html",
        "https://www.xiangqiqipu.com/Category/View-12028.html",
        "https://www.xiangqiqipu.com/Category/View-32189.html",
        "move_0_10_1",
        9,
        RED,
    ),
)


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def write_report(report):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{report['id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_source(candidate):
    path = DATA / candidate.html
    text = path.read_text(encoding="utf-8")
    tags = dict(re.findall(r"\[DhtmlXQ_(\w+)\](.*?)\[/DhtmlXQ_\1\]", text, re.S))
    raw = tags["movelist"]
    prefix = 0
    if candidate.branch != "main":
        parent, start, _ = map(int, candidate.branch.removeprefix("move_").split("_"))
        if parent != 0:
            raise ValueError("本轮只研究主线的直接分支")
        prefix = start - 1
        raw = raw[: prefix * 4] + tags[candidate.branch]
    raw = re.sub(r"\s+", "", raw)
    if len(raw) % 4 or not raw.isdigit():
        raise ValueError("公开棋谱数字着法不完整")
    moves = []
    for index in range(0, len(raw), 4):
        x, y, a, b = map(int, raw[index : index + 4])
        if x > 8 or a > 8:
            raise ValueError("公开棋谱坐标超出棋盘")
        moves.append(f"{chr(97 + x)}{9 - y}{chr(97 + a)}{9 - b}")
    metadata = {
        "url": candidate.url,
        "requested_category_url": candidate.category_url,
        "html": candidate.html,
        "html_sha256": digest(path),
        "title": tags.get("title", ""),
        "branch": candidate.branch,
        "main_prefix_plies": prefix,
        "dhtmlxq_numeric_moves": raw,
        "coordinate_conversion": "file=x; rank=9-y",
        "comments": {key: value for key, value in tags.items() if key.startswith("comment")},
    }
    return tuple(moves), metadata


def replay_main(moves):
    position = Position.start()
    positions = [position.copy()]
    steps = []
    for ply, text in enumerate(moves, 1):
        if game_result(position) is not None:
            raise ValueError(f"来源棋谱在第 {ply} 步前已结束")
        move = parse_iccs(text)
        cn = move_to_chinese(position.board, move)
        captured = position.board[move[1]]
        position.push(move)
        note = f"{'红' if positions[-1].turn == RED else '黑'}方{cn}。"
        if captured:
            note += f"吃掉{'黑' if captured < 0 else '红'}方{piece_name(captured)}。"
        if position.in_check():
            note += "将军，下一步须应将。"
        steps.append(TrapMove(ply, cn, text, position.fen(), note))
        positions.append(position.copy())
    return positions, tuple(steps)


class AuditedEngine:
    """保存每次实际 UCI 输入和适配器解析后的全部最终输出，失败也落盘。"""

    def __init__(self, engine, candidate_id, phase=None):
        self.engine = engine
        self.candidate_id = candidate_id
        suffix = f".{phase}" if phase else ""
        self.path = DATA / f"{candidate_id}{suffix}.searches.jsonl"
        self.count = 0

    async def analyse(self, fen, moves=(), *, limit, multipv=1, searchmoves=()):
        self.count += 1
        record = {
            "search": self.count,
            "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "input": {
                "fen": fen,
                "moves": list(moves),
                "limit": asdict(limit),
                "multipv": multipv,
                "searchmoves": list(searchmoves),
            },
        }
        print(
            f"{self.candidate_id}: search {self.count}, {limit.movetime_ms} ms, "
            f"history={len(moves)}, searchmoves={list(searchmoves)}",
            flush=True,
        )
        started = time.monotonic()
        try:
            result = await self.engine.analyse(
                fen,
                moves=moves,
                limit=limit,
                multipv=multipv,
                searchmoves=searchmoves,
            )
            record["output"] = asdict(result)
            first = result.lines[0] if result.lines else None
            print(
                f"  → {result.bestmove}, depth={first.depth if first else None}, "
                f"WDL={first.wdl if first else None}",
                flush=True,
            )
            return result
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def sacrifice_screen(engine, candidate, positions, moves, report):
    ply, side = candidate.trap_ply, candidate.side
    before, after, accepted = positions[ply - 1 : ply + 2]
    greedy = parse_iccs(moves[ply])
    offered = after.board[greedy[1]]
    report["sacrifice"] = {
        "trap_ply": ply,
        "trap_move": moves[ply - 1],
        "trap_cn": report["main_steps"][ply - 1]["cn"],
        "accept_ply": ply + 1,
        "accept_move": moves[ply],
        "accept_cn": report["main_steps"][ply]["cn"],
        "offered_piece": offered,
        "offered_name": piece_name(offered) if offered else None,
        "before_material_balance": _material(before, side),
        "after_accept_material_balance": _material(accepted, side),
        "material_method": "固定棋子价值，只统计实物吃子，不把兵过河的价值变化当作吃子",
    }
    if (
        before.turn != side
        or offered * side <= 0
        or abs(offered) not in (KNIGHT, CANNON, ROOK)
        or _material(accepted, side) >= _material(before, side)
    ):
        raise ValueError("陷阱接受点没有实际净吃掉执刀方的马、炮或车")
    rounds = []
    for duration in (5000, 15000, 30000):
        evidence: list[ScoreEvidence] = []
        best = await _score(
            engine,
            before,
            side,
            Limit(movetime_ms=duration),
            initial_fen=positions[0].fen(),
            label="弃子前最佳",
            evidence=evidence,
        )
        chosen = await _score(
            engine,
            before,
            side,
            Limit(movetime_ms=duration),
            (moves[ply - 1],),
            initial_fen=positions[0].fen(),
            label="限定弃子着法",
            evidence=evidence,
        )
        drop = max(0.0, best - chosen)
        rounds.append(
            {
                "movetime_ms": duration,
                "best_score": best,
                "sacrifice_score": chosen,
                "drop": drop,
                "score_gate": chosen >= 0.45 - EPS,
                "drop_gate": drop <= 0.10 + EPS,
                "evidence": [asdict(item) for item in evidence],
            }
        )
        report["sacrifice_screen"] = {"rounds": rounds, "status": "running"}
        write_report(report)
        print(
            f"{candidate.id}: screen {duration}: sacrifice={chosen:.4f}, drop={drop:.4f}",
            flush=True,
        )
        if duration == 15000:
            changed = any(rounds[0][key] != rounds[1][key] for key in ("score_gate", "drop_gate"))
            near = any(
                abs(item["sacrifice_score"] - 0.45) <= 0.02 + EPS
                or abs(item["drop"] - 0.10) <= 0.02 + EPS
                for item in rounds
            )
            if not changed and not near:
                break
    final = rounds[-1]
    if len(rounds) == 3 and any(
        rounds[-2][key] != final[key] for key in ("score_gate", "drop_gate")
    ):
        status = "pending"
    else:
        status = "passed" if final["score_gate"] and final["drop_gate"] else "rejected"
    report["sacrifice_screen"]["status"] = status
    return status


async def accepted_score(engine, position, side, report):
    evidence = []
    value = await _score(
        engine,
        position,
        side,
        Limit(movetime_ms=15000),
        initial_fen=Position.start().fen(),
        label="接受弃子后",
        evidence=evidence,
    )
    report["accepted_sacrifice"] = {"score": value, "evidence": [asdict(item) for item in evidence]}


async def generated_refute(engine, after, side, accepted_move, report):
    evidence = []
    await _score(
        engine,
        after,
        -side,
        Limit(movetime_ms=15000),
        initial_fen=Position.start().fen(),
        label="生成最佳防守PV",
        evidence=evidence,
    )
    best = evidence[-1]
    refute = best.pv[:6]
    report.setdefault("refute_generation", []).append(asdict(best))
    if len(refute) < 6:
        raise ValueError("引擎PV不足六个半回合，无法完整生成本轮破解变")
    if refute[0] == accepted_move:
        raise ValueError("最佳防守就是接受弃子，未产生正式库要求的不同破解首着")
    position = after.copy()
    cn = []
    for text in refute:
        move = parse_iccs(text)
        cn.append(move_to_chinese(position.board, move))
        position.push(move)
    return tuple(cn)


def formal_candidate(candidate, steps, refute):
    notes = [(step.ply, step.note) for step in steps]
    if candidate.id == "pingfengma-qima-xianche":
        custom = {
            7: "红车巡河，准备横向吃卒；后续吃卒过程属于诱导铺垫。",
            8: "黑方双马屏风，形成中炮巡河车对屏风马的布局。",
            16: "黑方挺7卒，继续引导红车深入。",
            17: "红车吃7卒，尚未接受弃马；不能把这步吃卒当作陷阱吃子点。",
            18: "黑象3进5，暂不保护7路马；红方下一步须决定是否吃马。",
            19: "红车吃7路马，实际接受黑方的弃马；后续马炮配合限制车的退路。",
            20: "另一匹黑马退5路，攻击深入的红车并组织封锁。",
            22: "黑炮沉到底线吃相，将军，红方必须应将。",
            24: "黑炮平1路隔底马吃掉红方左车；还须计算红车后续攻势，不能只凭得车断言必胜。",
        }
        notes = [(ply, custom.get(ply, note)) for ply, note in notes]
        intro = "黑方先用卒引红车深入，再放出7路马；红车吃马后，黑马和炮配合限制车的活动。"
        principle = (
            "识别陷阱的关键在象3进5之后：吃卒只是前奏，吃7路马才是接受弃子。"
            "先计算车的退路与黑炮底线将军。主变含后续教学应着，"
            "接受弃子后的评分另行记录，不能宣传吃马后必输。"
        )
    else:
        intro = f"公开棋谱中的{candidate.name}候选，具体收录以真实引擎逐步验证为准。"
        principle = "弃子须有实际补偿；完整主变和正确防守均由有限搜索复核。"
    return Trap(
        candidate.id,
        candidate.name,
        candidate.side,
        intro,
        principle,
        candidate.trap_ply,
        tuple(step.cn for step in steps),
        refute,
        tuple(notes),
        tuple(
            (candidate.trap_ply + i + 1, f"引擎最佳防守PV第 {i + 1} 半回合：{cn}。")
            for i, cn in enumerate(refute)
        ),
        candidate.url,
    )


async def verify_one(engine, candidate, metadata):
    moves, source = parse_source(candidate)
    positions, steps = replay_main(moves)
    report = {
        "id": candidate.id,
        "name": candidate.name,
        "kind": "candidate",
        "main_kind": "source_main",
        "policy_version": POLICY_VERSION,
        "input_fingerprint": candidate_input(candidate, moves, source),
        "status": "running",
        "engine": metadata,
        "source": source,
        "side": candidate.side,
        "main_iccs": list(moves),
        "main_steps": [asdict(step) for step in steps],
        "thresholds": {
            "sacrifice_min": 0.45,
            "sacrifice_drop_max": 0.10,
            "main_min": 0.75,
            "refute_min": 0.45,
            "defense_drop_max": 0.05,
        },
        "audit_log": f"{candidate.id}.searches.jsonl",
    }
    write_report(report)
    try:
        status = await sacrifice_screen(engine, candidate, positions, moves, report)
        if status != "passed":
            report["status"] = status
            last = report["sacrifice_screen"]["rounds"][-1]
            report["reason"] = (
                "15秒与30秒弃子门槛结论不一致，暂不收录"
                if status == "pending"
                else (
                    f"弃子后期望得分 {last['sacrifice_score']:.4f}，"
                    f"相对最佳下降 {last['drop']:.4f}；"
                    "未达到得分至少0.45、下降至多0.10的门槛，已构成不收录的充分理由"
                )
            )
            report["not_executed"] = [
                "主变终点胜势门槛",
                "破解首步及终点门槛",
                "每个防守着法下降门槛",
            ]
            # 接受后的分数用于区分诱错与正确应对，独立标注，不当作已完成完整验证。
            await accepted_score(engine, positions[candidate.trap_ply + 1], candidate.side, report)
        else:
            refute = await generated_refute(
                engine,
                positions[candidate.trap_ply],
                candidate.side,
                moves[candidate.trap_ply],
                report,
            )
            trap = formal_candidate(candidate, steps, refute)
            line = replay_trap(trap)
            result = await verify_trap(engine, line)
            report["verification"] = asdict(result)
            report["status"], report["reason"] = result.status, result.reason
            report["proposed_trap"] = asdict(trap)
            report["not_executed"] = []
            if result.passed:
                accepted = result.final.accepted_score
                # 接受后不达胜势门槛，说明主变后段依赖额外失误，不能宣传吃了必输。
                if accepted is not None and accepted < 0.75:
                    trap = replace(
                        trap,
                        principle=trap.principle + "这是连续失误的教学主变，吃马本身不保证必败。",
                    )
                report["published_trap"] = asdict(trap)
    except Exception as error:
        report["status"] = "pending"
        report["reason"] = f"验证未完成：{type(error).__name__}: {error}"
    report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    write_report(report)
    print(f"{candidate.id}: FINAL {report['status']} — {report['reason']}", flush=True)
    return report


async def verify_baseline(engine, metadata):
    line = TRAP_LINES["qima-shisan"]
    report = {
        "id": line.trap.id,
        "name": line.trap.name,
        "kind": "baseline",
        "main_kind": "source_main",
        "policy_version": POLICY_VERSION,
        "input_fingerprint": baseline_input(line),
        "status": "running",
        "engine": metadata,
        "source": {
            "url": line.trap.source,
            "branch": "main",
            "snapshot": "既有公开来源主变25半回合，保留原有ID和主变",
        },
        "main_iccs": list(line.moves),
        "main_steps": [asdict(step) for step in line.steps],
        "audit_log": f"{line.trap.id}.searches.jsonl",
    }
    write_report(report)
    try:
        original = await verify_trap(engine, line)
        report["original_verification"] = asdict(original)
        report["verification"] = asdict(original)
        result = original
        trap = line.trap
        if not result.passed and any("破解" in error for error in result.final.errors):
            after = Position.start()
            for move in line.moves[: line.trap.trap_ply]:
                after.push(parse_iccs(move))
            refute = await generated_refute(
                engine,
                after,
                line.trap.side,
                line.moves[line.trap.trap_ply],
                report,
            )
            trap = replace(
                line.trap,
                refute=refute,
                refute_notes=tuple(
                    (line.trap.trap_ply + i + 1, f"引擎重新生成的最佳防守PV：{cn}。")
                    for i, cn in enumerate(refute)
                ),
            )
            result = await verify_trap(engine, replay_trap(trap))
            report["verification"] = asdict(result)
        report["status"], report["reason"] = result.status, result.reason
        report["proposed_trap"] = asdict(trap)
        if result.passed:
            report["published_trap"] = asdict(trap)
    except Exception as error:
        report["status"] = "pending"
        report["reason"] = f"验证未完成：{type(error).__name__}: {error}"
    report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    write_report(report)
    print(f"qima-shisan: FINAL {report['status']} — {report['reason']}", flush=True)
    return report


async def verify_engine_main(engine, candidate, metadata, original):
    """只生成一次固定前缀+固定12半回合PV，不按好评分挑选终点。"""
    report = {
        "id": candidate.id,
        "name": candidate.name,
        "kind": "candidate",
        "main_kind": "engine_main",
        "policy_version": POLICY_VERSION,
        "status": "running",
        "engine": metadata,
        "source": original["source"],
        "side": candidate.side,
        "original_source_report": f"{candidate.id}.source-main.json",
        "source_main_status": original["status"],
        "audit_log": f"{candidate.id}.engine-main.searches.jsonl",
        "thresholds": original["thresholds"],
    }
    source_moves, source = parse_source(candidate)
    try:
        report["input_fingerprint"] = engine_main_input(candidate, source_moves, source, original)
        accepted = accepted_generation_evidence(original)
        prefix = tuple(source_moves[:19])
        if tuple(accepted["moves"]) != prefix or accepted["side"] != candidate.side:
            raise ValueError("原始30秒评分局面与来源前19半回合不一致")
        moves = list(prefix)
        position = Position.start()
        for text in prefix:
            position.push(parse_iccs(text))
        stop_reason = "已取固定12半回合"
        requested = tuple(accepted["pv"][:12])
        for text in requested:
            result = game_result(position)
            if result is not None:
                stop_reason = f"提前终局：{result.reason}"
                break
            try:
                position.push(parse_iccs(text))
            except ValueError as error:
                raise ValueError(f"引擎PV出现非法着法：{text}: {error}") from error
            moves.append(text)
        actual_plies = len(moves) - 19
        if actual_plies < 12 and game_result(position) is None:
            raise ValueError("原始30秒PV不足固定12半回合")
        _, steps = replay_main(moves)
        steps = tuple(
            replace(step, note=f"Pikafish固定PV第 {step.ply - 19} 半回合。{step.note}")
            if step.ply > 19
            else step
            for step in steps
        )
        refute = tuple(original["proposed_trap"]["refute"])
        trap = formal_candidate(candidate, steps, refute)
        trap = replace(
            trap,
            principle=(
                "来源棋谱保留前19半回合，吃马后固定取本机Pikafish30秒PV的前12半回合。"
                "破解由15秒最佳防守PV生成，再逐步复核。评分随搜索深度变化，"
                "吃马后的分数单独记录，不能宣传吃马必输。"
            ),
            notes=tuple(
                (ply, f"Pikafish固定续着。{note}" if ply > 19 else note) for ply, note in trap.notes
            ),
        )
        line = replay_trap(trap)
        report["main_iccs"] = list(line.moves)
        report["main_steps"] = [asdict(step) for step in line.steps]
        report["generation"] = {
            "source_prefix_plies": 19,
            "requested_pv_plies": 12,
            "actual_pv_plies": actual_plies,
            "attempts": 1,
            "stop_reason": stop_reason,
            "selected_pv": list(requested),
            "accepted_score_evidence": accepted,
            "origin": "原始source_main复核中的30秒接受弃子后真实PV；无新增生成搜索",
        }
        report["refute_generation"] = original["refute_generation"]
        report["proposed_trap"] = asdict(trap)
        write_report(report)
        result = await verify_trap(engine, line)
        report["verification"] = asdict(result)
        report["status"], report["reason"] = result.status, result.reason
        report["not_executed"] = []
        if result.passed:
            report["published_trap"] = asdict(trap)
    except Exception as error:
        report["status"] = "pending"
        report["reason"] = f"固定引擎续着验证未完成：{type(error).__name__}: {error}"
    report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    write_report(report)
    print(f"{candidate.id} engine_main: FINAL {report['status']} — {report['reason']}", flush=True)
    return report


async def retry_baseline(real, metadata):
    """仅为一次5秒上下界异常做完整独立重试；不修改棋谱或阈值。"""
    path = DATA / "qima-shisan.json"
    first_path = DATA / "qima-shisan.first-verification.json"
    if first_path.exists():
        raise ValueError("已经执行过一次基线独立重试，不再继续尝试")
    original = json.loads(path.read_text())
    rounds = original["verification"]["rounds"]
    if (
        original["status"] != "pending"
        or [item["movetime_ms"] for item in rounds] != [5000, 15000, 30000]
        or rounds[0]["check"]["failure_kind"] != "engine"
        or len(rounds[0]["check"]["errors"]) != 1
        or "分数边界" not in rounds[0]["check"]["errors"][0]
        or not all(item["check"]["passed"] for item in rounds[1:])
    ):
        raise ValueError("原始基线并非只有一次5秒分数边界异常，不能按此策略重试")
    required = ("binary_sha256", "network_sha256", "threads", "hash_mb", "checker_sha256")
    if any(original["engine"].get(key) != metadata.get(key) for key in required):
        raise ValueError("引擎、权重或搜索资源已变化，不能作为同参数独立重试")
    line = TRAP_LINES["qima-shisan"]
    if original["main_iccs"] != list(line.moves) or original["proposed_trap"]["refute"] != list(
        line.trap.refute
    ):
        raise ValueError("基线主变或破解已变化，不能作为同参数独立重试")
    first_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n")
    old_log = DATA / original["audit_log"]
    shutil.copy2(old_log, DATA / "qima-shisan.first-verification.searches.jsonl")
    audited = AuditedEngine(real, "qima-shisan", "retry")
    if audited.path.exists():
        raise ValueError("已经存在基线重试原始日志，保留现有证据并停止")
    report = {
        **original,
        "status": "running",
        "engine": metadata,
        "policy_version": POLICY_VERSION,
        "input_fingerprint": baseline_input(line),
        "first_verification": first_path.name,
        "first_verification_status": original["status"],
        "retry_number": 1,
        "retry_reason": "唯一阻碍是5秒upperbound，15/30所有真实门槛通过",
        "audit_log": audited.path.name,
    }
    report.pop("published_trap", None)
    report.pop("verification", None)
    report.pop("original_verification", None)
    write_report(report)
    result = await verify_trap(audited, line)
    report["verification"] = asdict(result)
    report["status"], report["reason"] = result.status, result.reason
    if result.passed:
        report["published_trap"] = asdict(line.trap)
    report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    write_report(report)
    print(f"qima-shisan independent retry: FINAL {result.status} — {result.reason}", flush=True)
    return report


def result_metrics(report):
    verification = report.get("verification")
    if verification:
        rounds = verification["rounds"]
        check = rounds[-1]["check"]
        return {
            "final_movetime_ms": rounds[-1]["movetime_ms"],
            "rounds_ms": [item["movetime_ms"] for item in rounds],
            "sacrifice_score": check["sacrifice_score"],
            "sacrifice_drop": check["sacrifice_drop"],
            "accepted_score": check["accepted_score"],
            "main_score": check["main_score"],
            "refute_entry_score": check["refute_entry_score"],
            "refute_score": check["refute_score"],
            "max_defender_drop": max(
                (item["drop"] for item in check["defense_checks"]),
                default=None,
            ),
            "final_round_gate_passed": check["passed"],
            "errors": check["errors"],
            "round_engine_errors": [
                {"movetime_ms": item["movetime_ms"], "errors": item["check"]["errors"]}
                for item in rounds
                if item["check"]["failure_kind"] == "engine"
            ],
        }
    screen = report.get("sacrifice_screen", {}).get("rounds", [])
    if not screen:
        return None
    return {
        "final_movetime_ms": screen[-1]["movetime_ms"],
        "rounds_ms": [item["movetime_ms"] for item in screen],
        "sacrifice_score": screen[-1]["sacrifice_score"],
        "sacrifice_drop": screen[-1]["drop"],
        "accepted_score": report.get("accepted_sacrifice", {}).get("score"),
        "main_score": None,
        "refute_entry_score": None,
        "refute_score": None,
        "max_defender_drop": None,
        "not_executed": report.get("not_executed", []),
    }


def aggregate():
    reports = []
    for candidate_id in [item.id for item in CANDIDATES] + ["qima-shisan"]:
        path = DATA / f"{candidate_id}.json"
        if path.exists():
            report = json.loads(path.read_text())
            item = {
                "id": candidate_id,
                "name": report["name"],
                "status": report["status"],
                "reason": report.get("reason", "验证正在进行"),
                "report": path.name,
                "source": report["source"]["url"],
                "main_kind": report.get("main_kind", "source_main"),
                "metrics": result_metrics(report),
            }
            original_path = DATA / report.get("original_source_report", "missing")
            if original_path.is_file():
                original = json.loads(original_path.read_text())
                item["original_source_report"] = original_path.name
                item["source_main_status"] = original["status"]
                item["source_main_metrics"] = result_metrics(original)
            first_path = DATA / report.get("first_verification", "missing")
            if first_path.is_file():
                first = json.loads(first_path.read_text())
                item["first_verification"] = first_path.name
                item["first_verification_status"] = first["status"]
                item["first_verification_metrics"] = result_metrics(first)
            reports.append(item)
    summary = {
        "validation_date": "2026-10-12",
        "policy_version": POLICY_VERSION,
        "engine_resources": {"threads": 4, "hash_mb": 256},
        "finite_search_notice": "有限搜索证据随引擎版本和深度变化，不是对所有变例的数学证明",
        "results": reports,
    }
    (DATA / "aggregate.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def run(args):
    DATA.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config).engine
    if config is None or config.flavor != "pikafish":
        raise ValueError("本轮必须使用已配置的本机Pikafish引擎")
    config = replace(config, threads=4, hash_mb=256)
    service = EngineService(config)
    try:
        real = await service.reviewer()
        network = config.eval_file or find_network(config.path)
        metadata = {
            "name": real.name,
            "flavor": config.flavor,
            "command": real.command,
            "threads": 4,
            "hash_mb": 256,
            "binary_sha256": digest(config.path),
            "network": str(network) if network else None,
            "network_sha256": digest(network) if network else None,
            "checker_sha256": digest(REPO / "backend" / "xiangqi" / "openings" / "check.py"),
            "script_sha256": digest(Path(__file__)),
            "adapter_output": (
                "每次搜索保留真实UCI输入与适配器解析后的完整最终输出，不伪造未执行结果"
            ),
        }
        selected = set(args.candidate or [item.id for item in CANDIDATES] + ["qima-shisan"])
        if args.retry_baseline:
            await retry_baseline(real, metadata)
            aggregate()
            return
        for candidate in CANDIDATES:
            if candidate.id not in selected:
                continue
            path = DATA / f"{candidate.id}.json"
            moves, source = parse_source(candidate)
            expected_input = candidate_input(candidate, moves, source)
            expected_kind = (
                "engine_main" if candidate.id == "pingfengma-qima-xianche" else "source_main"
            )
            source_path = DATA / f"{candidate.id}.source-main.json"
            original = None
            if expected_kind == "engine_main":
                expected_input = None
                if source_path.exists():
                    original = json.loads(source_path.read_text())
                    try:
                        expected_input = engine_main_input(candidate, moves, source, original)
                    except (ValueError, KeyError, TypeError):
                        # 来源生成证据缺失或损坏，不能复用旧的最终判定。
                        pass
            if not args.force and path.exists():
                old = json.loads(path.read_text())
                if expected_input is not None and cache_valid(
                    old,
                    metadata,
                    expected_input,
                    expected_kind,
                ):
                    print(f"{candidate.id}: reuse completed report {old['status']}", flush=True)
                    continue
            if args.engine_main and candidate.id == "pingfengma-qima-xianche":
                if original is None:
                    raise ValueError("--engine-main 要求先保存完整source-main.json证据")
                source_engine = original["engine"]
                required = ("binary_sha256", "network_sha256", "threads", "hash_mb")
                if (
                    original["source"]["html_sha256"] != source["html_sha256"]
                    or any(source_engine.get(key) != metadata.get(key) for key in required)
                    or original["main_iccs"] != list(moves)
                ):
                    raise ValueError("原始source_main的来源、棋谱或引擎资源已变化，须先重审来源")
                archive_previous(candidate.id, "engine-main")
                audited = AuditedEngine(real, candidate.id, "engine-main")
                if audited.path.exists():
                    audited.path.unlink()
                await verify_engine_main(audited, candidate, metadata, original)
                aggregate()
                continue
            archive_previous(candidate.id)
            audited = AuditedEngine(real, candidate.id)
            if audited.path.exists():
                audited.path.unlink()
            original = await verify_one(audited, candidate, metadata)
            if candidate.id == "pingfengma-qima-xianche" and "verification" in original:
                source_path = DATA / f"{candidate.id}.source-main.json"
                source_path.write_text(
                    json.dumps(original, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                audited = AuditedEngine(real, candidate.id, "engine-main")
                archive_previous(candidate.id, "engine-main")
                if audited.path.exists():
                    audited.path.unlink()
                await verify_engine_main(audited, candidate, metadata, original)
            aggregate()
        if "qima-shisan" in selected:
            path = DATA / "qima-shisan.json"
            if (
                not args.force
                and path.exists()
                and cache_valid(
                    json.loads(path.read_text()),
                    metadata,
                    baseline_input(TRAP_LINES["qima-shisan"]),
                    "source_main",
                )
            ):
                print("qima-shisan: reuse completed report", flush=True)
            else:
                archive_previous("qima-shisan")
                audited = AuditedEngine(real, "qima-shisan")
                if audited.path.exists():
                    audited.path.unlink()
                await verify_baseline(audited, metadata)
            aggregate()
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO / "config.toml"))
    parser.add_argument(
        "--candidate", action="append", choices=[item.id for item in CANDIDATES] + ["qima-shisan"]
    )
    parser.add_argument(
        "--force", action="store_true", help="重新运行真实搜索，替换对应候选的旧报告"
    )
    parser.add_argument(
        "--engine-main",
        action="store_true",
        help="使用保存的source-main30秒PV，只复核固定长度的屏风马引擎续着",
    )
    parser.add_argument(
        "--retry-baseline",
        action="store_true",
        help="仅为首次5秒upperbound异常执行一次同参数完整独立重试，保留首次pending",
    )
    args = parser.parse_args()
    if args.engine_main and args.retry_baseline:
        parser.error("--engine-main 和 --retry-baseline 不能同时使用")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
