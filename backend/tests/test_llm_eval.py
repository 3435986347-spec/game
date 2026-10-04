import asyncio
from collections import Counter

from xiangqi.llm import ExplainService, LLMConfig
from xiangqi.llm.evaluate import CATEGORIES, context_for, load_eval_set, render_report, run_eval
from xiangqi.llm.templates import template_explanation
from xiangqi.llm.validate import unknown_moves


def test_eval_set_shape():
    items = load_eval_set()
    assert len(items) == 30
    assert Counter(item["category"] for item in items) == {c: 10 for c in CATEGORIES}
    assert len({item["id"] for item in items}) == 30


def test_every_eval_position_builds_a_context_and_a_valid_template():
    for item in load_eval_set():
        ctx = context_for(item, "入门")
        assert ctx.move_cn in ctx.allowed, item["id"]
        assert ctx.data["engine_best"], item["id"]
        expl = template_explanation(ctx)
        assert unknown_moves(expl, ctx.allowed_keys) == [], item["id"]


def test_eval_report_with_template_provider():
    service = ExplainService(LLMConfig(provider="none"))
    results = asyncio.run(run_eval(service, load_eval_set()[:3]))
    report = render_report(service, results)
    assert "编造着法：0 次" in report and "退回模板讲解：3 个局面" in report
    assert report.count("- 评分：") == 3
