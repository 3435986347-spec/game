#!/usr/bin/env python3
"""只筛一条新增隐秘候选，另两条保留合法来源并待验证，不改既有取证。"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import verify_trap_candidates as framework
from verify_trap_candidates import (
    DATA,
    AuditedEngine,
    Candidate,
    accepted_score,
    candidate_input,
    digest,
    fingerprint,
    generated_refute,
    parse_source,
    replay_main,
    sacrifice_screen,
    write_report,
)

from xiangqi.config import load_config
from xiangqi.core import move_to_chinese, parse_iccs
from xiangqi.core.board import BLACK, RED
from xiangqi.engine import EngineService
from xiangqi.engine.uci import find_network
from xiangqi.openings.check import verify_trap
from xiangqi.openings.index import replay_trap
from xiangqi.openings.traps import Trap

ACTIVE = Candidate(
    "hidden-shunpao-shandian",
    "顺炮缓开车弃马攻象",
    "hidden-shunpao-shandian.html",
    "https://www.xiangqiqipu.com/Category/View-6041.html",
    "https://www.xiangqiqipu.com/Category/View-6041.html",
    "main",
    17,
    RED,
)
PENDING = (
    Candidate(
        "hidden-che-ru-huaxin",
        "车入花心",
        "hidden-che-ru-huaxin.html",
        "https://www.xiangqiqipu.com/Category/View-2704.html",
        "https://www.xiangqiqipu.com/Category/View-2704.html",
        "main",
        21,
        RED,
    ),
    Candidate(
        "hidden-tuipao-qima",
        "退炮弃马反击",
        "hidden-tuipao-qima.html",
        "https://www.xiangqiqipu.com/Category/View-2707.html",
        "https://www.xiangqiqipu.com/Category/View-2707.html",
        "main",
        20,
        BLACK,
    ),
)
SOURCE_DEFENSE = ("c9a7", "d4b5", "c7b9", "c6b6", "b4c4")


def initial_report(candidate, metadata=None):
    moves, source = parse_source(candidate)
    positions, steps = replay_main(moves)
    report = {
        "id": candidate.id,
        "name": candidate.name,
        "kind": "hidden_candidate",
        "main_kind": "source_main",
        "policy_version": framework.POLICY_VERSION,
        "input_fingerprint": candidate_input(candidate, moves, source),
        "status": "pending",
        "reason": "来源主变规则回放合法，尚未进行引擎验证",
        "engine": metadata or {},
        "source": source,
        "side": candidate.side,
        "main_iccs": list(moves),
        "main_steps": [asdict(step) for step in steps],
        "candidate_trap_ply": candidate.trap_ply,
        "accept_ply": candidate.trap_ply + 1,
        "not_executed": ["弃子门槛", "接受后评分", "主变胜势", "逐步破解门槛"],
        "thresholds": {
            "sacrifice_min": 0.45,
            "sacrifice_drop_max": 0.10,
            "main_min": 0.75,
            "refute_min": 0.45,
            "defense_drop_max": 0.05,
        },
        "audit_log": f"{candidate.id}.searches.jsonl",
    }
    if candidate.id == ACTIVE.id:
        position = positions[candidate.trap_ply].copy()
        steps_refute = []
        for text in SOURCE_DEFENSE:
            move = parse_iccs(text)
            cn = move_to_chinese(position.board, move)
            position.push(move)
            steps_refute.append({"iccs": text, "cn": cn, "fen": position.fen()})
        report["source_text_refute"] = {
            "origin": "来源正文comment18续着，不冒充Dhtml数字分支，尚未由引擎认证",
            "steps": steps_refute,
        }
    elif candidate.id == "hidden-che-ru-huaxin":
        report["reason"] = "仅规则合法；来源破解在20ply，早于实际21ply弃炮，须另研究弃子后防守"
        report["source_refute_branch"] = "move_0_20_1"
    else:
        report["reason"] = "仅规则合法；原文明确正确退马后黑方难抗衡，尚未进行引擎验证"
        report["source_refute_branch"] = "move_0_21_2"
    return report, positions, steps


def build_hidden_trap(steps, refute):
    annotations = {
        9: "双方形成顺炮缓开车布局，尚未出现弃子。",
        13: "红左炮平七路，后续准备攻击黑方底象。",
        16: "黑车升至马线，直接威胁六路红马。",
        17: "炮七进四吃卒，红方暂不救六路马；这才是弃子发动点。",
        18: "黑车平4路吃红马，实际接受弃子；须先算红炮攻象和横车出动。",
        19: "红炮进底线吃象，打开黑方将门附近的攻击线路。",
        21: "红左车抢占八路，准备沿开放线路加入攻势。",
        23: "红右车吃黑车；后续黑马吃回红车，属于车的交换。",
        31: "红炮平九，配合另一辆车继续攻将。",
        36: "黑车吃红仕，红方利用时间继续攻杀；不能单凭局部得子判断全局。",
        41: "来源主变终点为红车八路将军；是否达到胜势门槛由实时引擎另行评估。",
    }
    return Trap(
        ACTIVE.id,
        ACTIVE.name,
        RED,
        "红方用炮攻卒攻象，暂让黑车吃马，再抢出横车攻击黑方将门。",
        "重点看炮七进四后的车2平4：吃马前先计算红炮进底象和双车出动。"
        "主变为公开41半回合教学谱，破解由Pikafish生成并逐步验证；不能宣传吃马必输。",
        ACTIVE.trap_ply,
        tuple(step.cn for step in steps),
        refute,
        tuple((step.ply, annotations.get(step.ply, step.note)) for step in steps),
        tuple(
            (ACTIVE.trap_ply + index + 1, f"Pikafish固定破解PV：{cn}。")
            for index, cn in enumerate(refute)
        ),
        ACTIVE.url,
    )


def summary():
    results = []
    for candidate in (ACTIVE, *PENDING):
        path = DATA / f"{candidate.id}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        results.append(
            {
                "id": candidate.id,
                "name": candidate.name,
                "status": report["status"],
                "reason": report["reason"],
                "source": candidate.url,
                "report": path.name,
                "metrics": framework.result_metrics(report),
            }
        )
    (DATA / "hidden-aggregate.json").write_text(
        json.dumps(
            {"validation_date": "2026-10-12", "results": results}, ensure_ascii=False, indent=2
        )
        + "\n",
    )


async def execute(args):
    DATA.mkdir(parents=True, exist_ok=True)
    for candidate in PENDING:
        path = DATA / f"{candidate.id}.json"
        if not path.exists():
            report, _, _ = initial_report(candidate)
            write_report(report)
    if args.prepare_only:
        path = DATA / f"{ACTIVE.id}.json"
        if not path.exists():
            report, _, _ = initial_report(ACTIVE)
            write_report(report)
        summary()
        return
    config = load_config(args.config).engine
    if config is None or config.flavor != "pikafish":
        raise ValueError("必须使用已配置的本机Pikafish")
    config = replace(config, threads=4, hash_mb=256)
    service = EngineService(config)
    try:
        real = await service.reviewer()
        network = config.eval_file or find_network(config.path)
        driver_hash = digest(Path(__file__))
        framework_hash = digest(Path(framework.__file__))
        metadata = {
            "name": real.name,
            "flavor": config.flavor,
            "command": real.command,
            "threads": 4,
            "hash_mb": 256,
            "binary_sha256": digest(config.path),
            "network": str(network) if network else None,
            "network_sha256": digest(network) if network else None,
            "checker_sha256": digest(
                Path(framework.__file__).parents[1] / "xiangqi/openings/check.py"
            ),
            "script_sha256": fingerprint({"driver": driver_hash, "framework": framework_hash}),
            "driver_sha256": driver_hash,
            "framework_sha256": framework_hash,
        }
        report, positions, steps = initial_report(ACTIVE, metadata)
        path = DATA / f"{ACTIVE.id}.json"
        if path.exists() and not args.force:
            old = json.loads(path.read_text())
            if framework.cache_valid(old, metadata, report["input_fingerprint"], "source_main"):
                print(f"{ACTIVE.id}: reuse completed {old['status']}", flush=True)
                summary()
                return
        framework.archive_previous(ACTIVE.id)
        engine = AuditedEngine(real, ACTIVE.id)
        if engine.path.exists():
            engine.path.unlink()
        report["status"] = "running"
        write_report(report)
        try:
            status = await sacrifice_screen(
                engine, ACTIVE, positions, tuple(report["main_iccs"]), report
            )
            print(f"{ACTIVE.id}: SACRIFICE SCREEN FINAL {status}", flush=True)
            if status != "passed":
                report["status"] = status
                last = report["sacrifice_screen"]["rounds"][-1]
                report["reason"] = (
                    f"弃子得分{last['sacrifice_score']:.4f}/下降{last['drop']:.4f}，未达到弃子门槛"
                    if status == "rejected"
                    else "15/30秒弃子门槛结论仍不稳定"
                )
                report["not_executed"] = ["主变胜势", "破解首步和终点", "逐步防守下降"]
                await accepted_score(engine, positions[ACTIVE.trap_ply + 1], RED, report)
            else:
                refute = await generated_refute(
                    engine,
                    positions[ACTIVE.trap_ply],
                    RED,
                    report["main_iccs"][ACTIVE.trap_ply],
                    report,
                )
                trap = build_hidden_trap(steps, refute)
                result = await verify_trap(engine, replay_trap(trap))
                report["verification"] = asdict(result)
                report["proposed_trap"] = asdict(trap)
                report["status"], report["reason"] = result.status, result.reason
                report["not_executed"] = []
                if result.passed:
                    report["published_trap"] = asdict(trap)
        except Exception as error:
            report["status"] = "pending"
            report["reason"] = f"验证未完成：{type(error).__name__}: {error}"
        report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        write_report(report)
        summary()
        print(f"{ACTIVE.id}: FINAL {report['status']} — {report['reason']}", flush=True)
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(framework.REPO / "config.toml"))
    parser.add_argument(
        "--prepare-only", action="store_true", help="只保存合法來源和待验证状态，不启动引擎"
    )
    parser.add_argument("--force", action="store_true", help="保留旧证据后重审唯一活动候选")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
