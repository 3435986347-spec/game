"""大模型适配层的公共部分：统一接口、讲解的输出结构、配置、错误类型。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)

PROVIDERS = ("none", "claude", "openai_compat")


class Explanation(BaseModel):
    """讲解。两种大模型和模板讲解都按这个结构输出。"""

    headline: str = Field(description="一句话结论")
    why: str = Field(description="原因")
    better: str = Field(description="更好的下法及理由；这步已是最佳时说明为什么好")
    principle: str = Field(description="以后用得上的原则")
    tags: list[str] = Field(description="标签，如 无根子、贪吃，用于归类")


class GameSummaryText(BaseModel):
    """名局解读的分阶段总结（docs 5.5 节）。"""

    opening: str = Field(description="开局：布局名称和双方的思路")
    middlegame: str = Field(description="中局：主要计划和转折点")
    endgame: str = Field(description="残局（或终局）：胜负的关键")
    overall: str = Field(description="一句话总评")


class LLMError(RuntimeError):
    """调用大模型失败（网络、Key、限流、拒答等）。message 直接显示给用户。"""


class LLMFormatError(LLMError):
    """大模型的输出不是要求的 JSON 结构。可以重试。"""


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T: ...


@dataclass(frozen=True)
class ClaudeSettings:
    model: str = "claude-opus-5-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    # low | medium | high | xhigh | max；claude-opus-5-5 默认就是 medium，这里显式设置
    effort: str = "medium"
    fallbacks: bool = True  # 拒答时由服务端自动换模型重试（server-side fallback）


@dataclass(frozen=True)
class OpenAICompatSettings:
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = ""  # 按服务商文档填写，不写死在代码里
    json_mode: bool = True  # 部分推理模型不支持 JSON 模式，设为 false 后靠解析 + 校验兜底


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "none"  # none（只用模板讲解）| claude | openai_compat
    level: str = "入门"  # 学生水平，决定讲解深浅
    timeout_s: float = 90.0
    claude: ClaudeSettings = field(default_factory=ClaudeSettings)
    openai_compat: OpenAICompatSettings = field(default_factory=OpenAICompatSettings)

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"[llm] provider 应为 {' / '.join(PROVIDERS)}，实际为 {self.provider!r}"
            )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """从模型输出中取出 JSON 对象：去掉 ```json 包裹，取第一个完整的 {...}。"""
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise LLMFormatError("大模型的输出里没有 JSON")
    try:
        _, end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise LLMFormatError(f"大模型输出的 JSON 无法解析：{e}") from e
    return text[start : start + end]
