import asyncio
from types import SimpleNamespace

import pytest
from conftest import play

from xiangqi.core import Position, parse_iccs
from xiangqi.library import Library
from xiangqi.llm import (
    ClaudeSettings,
    EngineLine,
    ExplainService,
    Explanation,
    LLMConfig,
    LLMError,
    LLMFormatError,
    OpenAICompatSettings,
    build_context,
)
from xiangqi.llm.base import extract_json
from xiangqi.llm.claude import ClaudeProvider
from xiangqi.llm.openai_compat import OpenAICompatProvider
from xiangqi.llm.templates import template_explanation
from xiangqi.llm.validate import find_moves, move_key, unknown_moves


def blunder_context(level: str = "入门"):
    """docs 3.5 节的例子：1. 炮二平五 马8进7 2. 炮五进四（炮吃中卒后被马吃回）。"""
    pos = play(Position.start(), "h2e2", "h9g7")
    before = [
        EngineLine("h0g2", ("h0g2", "i9h9", "i0h0"), 0.54),
        EngineLine("b0c2", ("b0c2",), 0.53),
        EngineLine("g3g4", ("g3g4",), 0.52),
    ]
    after = [EngineLine("g7e6", ("g7e6", "h0g2"), 0.84)]
    return build_context(
        pos, parse_iccs("e2e6"), before=before, after=after,
        win_before=0.54, win_after=0.16, grade="漏着", level=level,
    )  # fmt: skip


def explanation(text: str = "这步炮吃中卒是送子。", better: str = "应该走马二进三。"):
    return Explanation(
        headline=text, why="黑马可以吃回。", better=better, principle="先看落点。", tags=["无根子"]
    )


# ---- 上下文 ----


def test_context_matches_design_example():
    ctx = blunder_context()
    data = ctx.data
    assert data["player"] == {"side": "红方", "level": "入门"}
    assert data["phase"] == "开局"
    assert data["move_played"] == {
        "cn": "炮五进四", "iccs": "e2e6", "win_prob_before": 0.54, "win_prob_after": 0.16,
        "grade": "漏着",
    }  # fmt: skip
    assert [e["cn"] for e in data["engine_best"]] == ["马二进三", "马八进七", "兵三进一"]
    assert data["opponent_reply"]["cn"] == "马7进5"
    assert "吃掉红方的炮(e6)" in data["opponent_reply"]["effect"]
    assert data["allowed_moves_cn"] == [
        "炮五进四", "马二进三", "车9平8", "车一平二", "马八进七", "兵三进一", "马7进5",
    ]  # fmt: skip
    facts = " / ".join(data["facts"])
    assert "炮五进四吃掉了黑方的卒(e6)" in facts
    assert "无根子" in facts and "净亏约3.5分" in facts
    assert {"无根子", "贪吃", "亏子", "开局"} <= set(ctx.tags)
    assert data["board_text"][1] == "9 車馬象士将士象．車"
    assert "炮(e6)" not in data["pieces"]["红方"] and "炮(e2)" in data["pieces"]["红方"]


def test_context_mate_threat_moves_are_allowed():
    # 红车 b1 沉底就能将死：走车 a8 封住第 8 行之后，「对方不应就一步杀」
    pos = Position.from_fen("4k4/9/R8/9/9/9/9/9/1R7/3K5 w - - 0 30")
    ctx = build_context(
        pos, parse_iccs("a7a8"), before=[], after=[], win_before=None, win_after=None,
        grade=None, level="入门",
    )  # fmt: skip
    assert "杀棋威胁" in ctx.tags
    assert "车八进八" in ctx.allowed
    assert any("车八进八就能将死对方" in f for f in ctx.facts)


def test_template_uses_only_allowed_moves():
    ctx = blunder_context()
    expl = template_explanation(ctx)
    assert expl.headline.startswith("炮五进四是漏着")
    assert "马二进三" in expl.better
    assert unknown_moves(expl, ctx.allowed_keys) == []
    assert expl.principle.startswith("走子前先看落点")


# ---- 校验 ----


def test_find_moves_and_keys():
    text = "这步炮五进四不好，應該馬二進三；黑方可以前炮平4，或者车1平2。"
    assert find_moves(text) == ["炮五进四", "馬二進三", "前炮平4", "车1平2"]
    assert move_key("馬8進7") == move_key("马8进7")
    assert move_key("马8进7") != move_key("马八进七")  # 红黑的数字写法不同，是两步棋


def test_unknown_moves_flags_fabricated_and_wrong_side_notation():
    ctx = blunder_context()
    ok = explanation(better="应该走马二进三，黑方会馬7進5。")
    assert unknown_moves(ok, ctx.allowed_keys) == []
    bad = explanation(better="应该走车二进六，或者马2进3。")
    assert unknown_moves(bad, ctx.allowed_keys) == ["车二进六", "马2进3"]


def test_extract_json():
    assert extract_json('好的：```json\n{"a": 1}\n``` 以上') == '{"a": 1}'
    assert extract_json('前缀 {"a": {"b": 2}} 后缀 {"c": 3}') == '{"a": {"b": 2}}'
    with pytest.raises(LLMFormatError):
        extract_json("没有 JSON")


# ---- 讲解流水线 ----


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls: list[str] = []

    async def complete_json(self, system, user, schema):
        self.calls.append(user)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def run(coro):
    return asyncio.run(coro)


def test_pipeline_success_and_cache():
    library = Library(":memory:")
    provider = FakeProvider(explanation())
    service = ExplainService(LLMConfig(provider="claude"), cache=library, provider=provider)
    first = run(service.explain(blunder_context()))
    assert first.source == "llm" and first.attempts == 1 and not first.cached
    assert "allowed_moves_cn" in provider.calls[0]
    again = run(service.explain(blunder_context()))  # 同一局面同一步：读缓存，不再调用
    assert again.cached and again.explanation == first.explanation and len(provider.calls) == 1
    other_level = run(service.explain(blunder_context(level="高级")))
    assert other_level.source == "template"  # 水平不同 → 缓存未命中，假模型已没有输出 → 出错
    library.close()


def test_pipeline_retries_once_after_fabricated_move():
    provider = FakeProvider(explanation(better="应该走车二进六。"), explanation())
    result = run(ExplainService(LLMConfig(), provider=provider).explain(blunder_context()))
    assert result.source == "llm" and result.attempts == 2
    assert result.fabricated == ["车二进六"]
    assert "车二进六" in provider.calls[1] and "allowed_moves_cn 之外的着法" in provider.calls[1]


def test_pipeline_falls_back_to_template_after_two_bad_outputs():
    provider = FakeProvider(explanation(better="车二进六"), explanation(better="车二进七"))
    result = run(ExplainService(LLMConfig(), provider=provider).explain(blunder_context()))
    assert result.source == "template" and result.attempts == 2
    assert result.fabricated == ["车二进六", "车二进七"]
    assert "没有通过校验" in result.note
    assert result.provider == "fake"


def test_pipeline_does_not_retry_on_api_error_but_retries_format_error():
    provider = FakeProvider(LLMError("API Key 无效"))
    result = run(ExplainService(LLMConfig(), provider=provider).explain(blunder_context()))
    assert result.source == "template" and result.note == "API Key 无效" and result.attempts == 1

    provider = FakeProvider(LLMFormatError("不是 JSON"), explanation())
    result = run(ExplainService(LLMConfig(), provider=provider).explain(blunder_context()))
    assert result.source == "llm" and result.attempts == 2


def test_no_provider_uses_template():
    service = ExplainService(LLMConfig(provider="none"))
    result = run(service.explain(blunder_context()))
    assert result.source == "template" and result.attempts == 0
    status = service.status()
    assert status["provider"] == "none" and status["ready"] is False and status["model"] is None


def test_openai_compat_needs_model():
    service = ExplainService(LLMConfig(provider="openai_compat"))
    assert service.provider is None and "model" in service.status()["problem"]


# ---- 适配器（不联网：替换掉 SDK 客户端） ----


def test_claude_provider_request_shape_and_refusal(monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    provider = ClaudeProvider(ClaudeSettings(api_key_env="TEST_ANTHROPIC_KEY", effort="high"))
    requests = []

    async def parse(**kwargs):
        requests.append(kwargs)
        return responses.pop(0)

    provider._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    good = explanation()
    responses = [
        SimpleNamespace(stop_reason="end_turn", parsed_output=good, stop_details=None),
        SimpleNamespace(
            stop_reason="refusal", parsed_output=None,
            stop_details=SimpleNamespace(category="cyber"),
        ),
    ]  # fmt: skip
    assert run(provider.complete_json("系统", "用户", Explanation)) == good
    req = requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_format"] is Explanation
    assert req["output_config"] == {"effort": "high"}
    assert req["betas"] == ["server-side-fallback-2026-07-01"] and req["fallbacks"] == "default"
    assert req["system"] == "系统" and req["messages"] == [{"role": "user", "content": "用户"}]
    with pytest.raises(LLMError, match="拒绝回答.*cyber"):
        run(provider.complete_json("系统", "用户", Explanation))


def test_openai_compat_provider_parses_json(monkeypatch):
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "sk-test")
    settings = OpenAICompatSettings(api_key_env="TEST_DEEPSEEK_KEY", model="some-model")
    provider = OpenAICompatProvider(settings)
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        content = "```json\n" + explanation().model_dump_json() + "\n```"
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    assert run(provider.complete_json("系统", "用户", Explanation)) == explanation()
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["model"] == "some-model"


def test_openai_compat_without_key(monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY_ENV", raising=False)
    provider = OpenAICompatProvider(OpenAICompatSettings(api_key_env="NO_SUCH_KEY_ENV", model="m"))
    with pytest.raises(LLMError, match="NO_SUCH_KEY_ENV"):
        run(provider.complete_json("系统", "用户", Explanation))
