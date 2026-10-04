"""讲解评测（docs 4.6 节）：换模型或改提示词之前，先在固定的评测集上跑一遍。

评测集 eval_set.json：30 个局面（开局漏着、中局战术、残局技巧各 10 个），来自 Pikafish 自对弈
（生成方法见 scripts/make_llm_eval_set.py）。每个局面冻结了引擎分析（候选着法、对方应着、胜率），
评测时只重新计算规则引擎的事实，所以不需要象棋引擎，结果只取决于大模型和提示词。

统计：
- 编造着法：大模型输出里出现 allowed_moves_cn 之外着法的次数（验收标准：0）。
  被校验拦下后会重试 1 次、仍不合格就退回模板，所以界面上不会出现编造的着法；这里统计的是模型本身。
- 退回模板的局面数、平均耗时；
- 报告里每个局面留一栏，人工给讲解的正确性和易懂程度打 1–5 分。

用法：cd backend && uv run xiangqi-llm-eval [--limit N] [--category 开局漏着] [--out 报告.md]
报告默认写到 data/llm-eval/。会实际调用大模型（产生费用），不读也不写讲解缓存。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ..core import Position, parse_iccs
from .context import EngineLine, build_context
from .service import ExplainResult, ExplainService

EVAL_SET = Path(__file__).with_name("eval_set.json")
CATEGORIES = ("开局漏着", "中局战术", "残局技巧")


def load_eval_set(path: Path = EVAL_SET) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["positions"]


def context_for(item: dict, level: str):
    pos = Position.from_fen(item["fen"])
    return build_context(
        pos,
        parse_iccs(item["move"]),
        before=[EngineLine.from_dict(d) for d in item["before"]],
        after=[EngineLine.from_dict(d) for d in item["after"]],
        win_before=item["win_before"],
        win_after=item["win_after"],
        grade=item["grade"],
        level=level,
    )


async def run_eval(service: ExplainService, items: list[dict]) -> list[tuple[dict, ExplainResult]]:
    results = []
    for i, item in enumerate(items, 1):
        ctx = context_for(item, service.level)
        result = await service.explain(ctx, use_cache=False)
        flag = f"编造 {len(result.fabricated)}" if result.fabricated else "ok"
        source = "模板" if result.source == "template" else "大模型"
        print(f"[{i}/{len(items)}] {item['id']} {ctx.move_cn}：{source}，{flag}，"
              f"{result.elapsed:.1f} 秒", file=sys.stderr)  # fmt: skip
        results.append((item, result))
    return results


def render_report(service: ExplainService, results: list[tuple[dict, ExplainResult]]) -> str:
    status = service.status()
    n = len(results)
    fabricated_positions = sum(1 for _, r in results if r.fabricated)
    fabricated_total = sum(len(r.fabricated) for _, r in results)
    template = sum(1 for _, r in results if r.source == "template")
    avg = sum(r.elapsed for _, r in results) / n if n else 0.0
    lines = [
        f"# 讲解评测 {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- 大模型：{status['provider']}（{status['model'] or '无'}），学生水平：{status['level']}",
        f"- 局面数：{n}",
        f"- **编造着法：{fabricated_total} 次，涉及 {fabricated_positions} 个局面**（验收标准：0）",
        f"- 退回模板讲解：{template} 个局面",
        f"- 平均耗时：{avg:.1f} 秒",
        "",
        "人工评分：每个局面按「讲解是否正确、是否易懂」打 1–5 分，填在「评分」一栏。",
        "",
    ]
    by_category: dict[str, list[tuple[dict, ExplainResult]]] = {}
    for item, result in results:
        by_category.setdefault(item["category"], []).append((item, result))
    for category, rows in by_category.items():
        lines += [f"## {category}", ""]
        for item, r in rows:
            ctx = context_for(item, service.level)
            e = r.explanation
            source = "模板" if r.source == "template" else "大模型"
            lines += [
                f"### {item['id']}：{ctx.move_cn}（{item['grade']}，"
                f"{round(item['win_before'] * 100)}% → {round(item['win_after'] * 100)}%）",
                "",
                f"FEN：`{item['fen']}`",
                "",
                f"- 来源：{source}，调用 {r.attempts} 次，{r.elapsed:.1f} 秒",
            ]
            if r.fabricated:
                lines.append(f"- **编造的着法**：{'、'.join(r.fabricated)}")
            if r.note:
                lines.append(f"- 说明：{r.note}")
            lines += [
                f"- **{e.headline}**",
                f"- 原因：{e.why}",
                f"- 更好的下法：{e.better}",
                f"- 原则：{e.principle}",
                f"- 标签：{'、'.join(e.tags)}",
                "- 评分：",
                "",
            ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="xiangqi-llm-eval", description="讲解评测（会调用大模型）"
    )
    parser.add_argument("--limit", type=int, help="只评测前 N 个局面")
    parser.add_argument("--category", choices=CATEGORIES, help="只评测一类局面")
    parser.add_argument("--out", help="报告文件（默认 data/llm-eval/<时间>-<provider>.md）")
    parser.add_argument("--config", help="配置文件路径")
    args = parser.parse_args(argv)

    from ..config import REPO_ROOT, load_config  # 避免循环导入

    config = load_config(args.config)
    service = ExplainService(config.llm)
    status = service.status()
    if config.llm.provider == "none":
        print(
            '提示：config.toml 中 [llm] provider = "none"，下面评测的是模板讲解。', file=sys.stderr
        )
    elif status["problem"]:
        print(f"提示：{status['problem']}", file=sys.stderr)

    items = load_eval_set()
    if args.category:
        items = [item for item in items if item["category"] == args.category]
    if args.limit:
        items = items[: args.limit]
    started = time.monotonic()
    results = asyncio.run(run_eval(service, items))
    report = render_report(service, results)

    out = Path(args.out) if args.out else (
        REPO_ROOT / "data" / "llm-eval" / f"{datetime.now():%Y%m%d-%H%M%S}-{config.llm.provider}.md"
    )  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    fabricated = sum(len(r.fabricated) for _, r in results)
    elapsed = time.monotonic() - started
    print(
        f"\n完成：{len(results)} 个局面，编造着法 {fabricated} 次，用时 {elapsed:.0f} 秒。"
        f"\n报告：{out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
