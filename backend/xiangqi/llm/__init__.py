"""大模型讲解：统一接口（OpenAI 兼容 / Claude / 只用模板）、讲解上下文、
着法白名单校验、模板兜底、缓存。"""

from .base import (
    ClaudeSettings,
    Explanation,
    GameSummaryText,
    LLMConfig,
    LLMError,
    LLMFormatError,
    LLMProvider,
    OpenAICompatSettings,
)
from .context import EngineLine, ExplainContext, SummaryContext, build_context
from .service import ExplainResult, ExplainService, make_provider

__all__ = [
    "ClaudeSettings",
    "EngineLine",
    "ExplainContext",
    "ExplainResult",
    "ExplainService",
    "Explanation",
    "GameSummaryText",
    "LLMConfig",
    "LLMError",
    "LLMFormatError",
    "LLMProvider",
    "OpenAICompatSettings",
    "SummaryContext",
    "build_context",
    "make_provider",
]
