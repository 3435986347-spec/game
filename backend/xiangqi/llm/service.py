"""讲解流水线（docs 4.5 节），与具体模型无关：

构建上下文 → 查缓存（任务 + 局面 + 着法 + 评级 + 水平 + 模型）→ provider.complete_json
→ pydantic 校验结构 → 着法白名单校验 → 不通过时把问题告诉模型、重试 1 次
→ 仍不通过 / 网络错误 / 没配 Key：使用模板讲解 → 大模型的讲解写入缓存

任务（TASKS）：讲解一步棋的对错（explain）、推断意图（intent）、整盘分阶段总结（summary），
各有自己的提示词、输出结构和模板，流水线相同。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .base import Explanation, GameSummaryText, LLMConfig, LLMError, LLMFormatError, LLMProvider
from .context import ExplainContext, SummaryContext
from .prompts import (
    INTENT_PROMPT,
    PROMPT_VERSION,
    SUMMARY_PROMPT,
    SYSTEM_PROMPT,
    retry_message,
    user_message,
)
from .templates import template_explanation, template_intent, template_summary
from .validate import problems, unknown_moves

logger = logging.getLogger(__name__)

_ATTEMPTS = 2  # 第一次 + 校验不通过时重试 1 次


@dataclass(frozen=True)
class Task:
    name: str
    system: str
    schema: type[BaseModel]
    template: Callable[[Any], BaseModel]


TASKS = {
    "explain": Task("explain", SYSTEM_PROMPT, Explanation, template_explanation),
    "intent": Task("intent", INTENT_PROMPT, Explanation, template_intent),
    "summary": Task("summary", SUMMARY_PROMPT, GameSummaryText, template_summary),
}


class ExplainCache(Protocol):
    def get_explanation(self, key: str) -> dict | None: ...

    def put_explanation(self, key: str, content: dict, provider: str, model: str) -> None: ...


@dataclass
class ExplainResult:
    explanation: BaseModel  # Explanation；summary 任务为 GameSummaryText
    source: str  # llm | template
    provider: str | None = None
    model: str | None = None
    cached: bool = False
    note: str | None = None  # 使用模板讲解的原因
    attempts: int = 0  # 调用了几次大模型
    fabricated: list[str] = field(default_factory=list)  # 大模型编造的着法（被校验拦下的）
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            **self.explanation.model_dump(),
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "note": self.note,
        }


def make_provider(config: LLMConfig) -> tuple[LLMProvider | None, str | None]:
    """按配置创建大模型适配器。返回 (适配器, 不可用的原因)；provider = "none" 时都为 None。"""
    if config.provider == "claude":
        from .claude import ClaudeProvider  # 用到时才导入 SDK

        if not config.claude.model:
            return None, "config.toml 的 [llm.claude] 没有填写 model"
        return ClaudeProvider(config.claude, timeout=config.timeout_s), None
    if config.provider == "openai_compat":
        from .openai_compat import OpenAICompatProvider

        s = config.openai_compat
        if not s.model:
            return None, (
                "config.toml 的 [llm.openai_compat] 没有填写 model（按服务商文档填写模型名）"
            )
        if not s.base_url:
            return None, "config.toml 的 [llm.openai_compat] 没有填写 base_url"
        return OpenAICompatProvider(s, timeout=config.timeout_s), None
    return None, None


def cache_key(ctx: ExplainContext | SummaryContext, provider: LLMProvider, task: str) -> str:
    text = "|".join(
        [PROMPT_VERSION, task, ctx.cache_id(), ctx.level, provider.name, provider.model]
    )
    return hashlib.sha1(text.encode()).hexdigest()


class ExplainService:
    def __init__(
        self,
        config: LLMConfig,
        cache: ExplainCache | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.config = config
        self.cache = cache
        if provider is not None:  # 测试时直接传入
            self.provider, self.problem = provider, None
        else:
            self.provider, self.problem = make_provider(config)

    @property
    def level(self) -> str:
        return self.config.level

    def status(self) -> dict:
        """供界面显示的配置状态（不实际调用大模型，免得花钱）。"""
        p = self.provider
        ready, problem = False, None
        if p is None:
            problem = self.problem or (
                '未配置大模型（config.toml 中 [llm] provider = "none"），讲解使用模板'
            )
        elif getattr(p, "has_key", True):
            ready = True
        elif p.name == "claude":
            # anthropic SDK 还会用 `ant auth login` 的登录信息，没设环境变量不一定不能用
            ready = True
            env = self.config.claude.api_key_env
            problem = f"没有设置环境变量 {env}，将使用 ant auth login 的登录信息"
        else:
            problem = f"没有设置环境变量 {self.config.openai_compat.api_key_env}"
        return {
            "provider": self.config.provider,
            "model": p.model if p is not None else None,
            "ready": ready,
            "problem": problem,
            "level": self.config.level,
        }

    async def explain(
        self,
        ctx: ExplainContext | SummaryContext,
        *,
        use_cache: bool = True,
        task: str = "explain",
    ) -> ExplainResult:
        spec = TASKS[task]
        started = time.monotonic()
        provider = self.provider
        if provider is None:
            note = self.problem or "未配置大模型"
            return self._template(spec, ctx, note, started)

        key = cache_key(ctx, provider, task)
        if use_cache and self.cache is not None:
            hit = await asyncio.to_thread(self.cache.get_explanation, key)
            try:
                cached = spec.schema.model_validate(hit) if hit is not None else None
            except ValidationError:  # 旧格式或损坏的缓存：当作没有缓存，重新生成后覆盖
                cached = None
            if cached is not None:
                return ExplainResult(
                    cached,
                    "llm",
                    provider.name,
                    provider.model,
                    cached=True,
                    elapsed=time.monotonic() - started,
                )

        original = user_message(ctx.data, task)
        user = original
        fabricated: list[str] = []
        note: str | None = None
        attempts = 0
        for _ in range(_ATTEMPTS):
            attempts += 1
            try:
                explanation = await asyncio.wait_for(
                    provider.complete_json(spec.system, user, spec.schema),
                    self.config.timeout_s + 10,
                )
            except LLMFormatError as e:  # 结构不对：可以重试
                note = str(e)
                continue
            except LLMError as e:  # Key、网络、拒答：重试也没用
                note = str(e)
                break
            except TimeoutError:
                note = "大模型响应超时"
                break
            except Exception as e:  # 适配器之外的意外错误：记日志，退回模板，不影响复盘
                logger.exception("调用大模型出错")
                note = f"调用大模型出错：{e}"
                break
            issues = problems(explanation, ctx.allowed_keys)
            if not issues:
                if self.cache is not None:
                    await asyncio.to_thread(
                        self.cache.put_explanation,
                        key,
                        explanation.model_dump(),
                        provider.name,
                        provider.model,
                    )
                return ExplainResult(
                    explanation,
                    "llm",
                    provider.name,
                    provider.model,
                    attempts=attempts,
                    fabricated=fabricated,
                    elapsed=time.monotonic() - started,
                )
            fabricated += [
                m for m in unknown_moves(explanation, ctx.allowed_keys) if m not in fabricated
            ]
            note = "大模型的讲解没有通过校验：" + "；".join(issues)
            user = retry_message(original, explanation, issues)

        result = self._template(spec, ctx, note, started)
        result.provider, result.model = provider.name, provider.model
        result.attempts, result.fabricated = attempts, fabricated
        return result

    def _template(
        self, spec: Task, ctx: ExplainContext | SummaryContext, note: str | None, started: float
    ) -> ExplainResult:
        return ExplainResult(
            spec.template(ctx), "template", note=note, elapsed=time.monotonic() - started
        )
