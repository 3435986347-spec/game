"""Claude 适配：官方 anthropic SDK，结构化输出（messages.parse + pydantic）。

- 默认模型 claude-opus-5-5：思考不能关闭，用 output_config.effort 控制深度
  （该模型默认 medium，这里显式设置）。
- 安全分类器可能拒答（stop_reason == "refusal"）：默认开启服务端回退（fallbacks="default"），
  由服务端按拒答类别换模型重试；整条回退链都拒答时才算失败。
- Key 从配置的环境变量读取；没有设置时交给 SDK 自己找
  （ANTHROPIC_API_KEY、`ant auth login` 的登录信息）。
"""

from __future__ import annotations

import os

import anthropic
from pydantic import ValidationError

from .base import ClaudeSettings, LLMError, LLMFormatError, T

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_TOKENS = 16000  # 含思考；讲解本身只有几百字


class ClaudeProvider:
    name = "claude"

    def __init__(self, settings: ClaudeSettings, *, timeout: float = 90.0) -> None:
        self.settings = settings
        self.model = settings.model
        self.timeout = timeout
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def has_key(self) -> bool:
        return bool(os.environ.get(self.settings.api_key_env))

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            key = os.environ.get(self.settings.api_key_env)
            try:
                self._client = (
                    anthropic.AsyncAnthropic(api_key=key, timeout=self.timeout)
                    if key
                    else anthropic.AsyncAnthropic(timeout=self.timeout)
                )
            except anthropic.AnthropicError as e:
                raise LLMError(self._no_key_message(e)) from e
        return self._client

    def _no_key_message(self, detail: object = "") -> str:
        text = (
            f"没有找到 Claude 的 API Key：请设置环境变量 {self.settings.api_key_env}"
            "（可以写在仓库根目录的 .env 文件里），或运行 ant auth login 登录"
        )
        return f"{text}（{detail}）" if detail else text

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        client = self._get_client()
        request = dict(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": self.settings.effort},
        )
        try:
            if self.settings.fallbacks:
                response = await client.beta.messages.parse(
                    **request, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await client.messages.parse(**request)
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Claude API Key 无效（{e.message}）") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"这个 API Key 没有权限调用 {self.model}（{e.message}）") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"找不到模型 {self.model}，请检查 config.toml（{e.message}）") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API 请求太频繁或额度不足，请稍后再试") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API 返回错误 {e.status_code}：{e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"无法连接 Claude API（网络问题或超时）：{e}") from e
        except ValidationError as e:
            raise LLMFormatError(f"Claude 的输出不符合要求的结构：{e}") from e
        except anthropic.AnthropicError as e:
            raise LLMError(f"调用 Claude 失败：{e}") from e
        except TypeError as e:  # SDK 找不到任何认证信息时抛 TypeError
            if "authentication" in str(e):
                raise LLMError(self._no_key_message()) from e
            raise

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"Claude 拒绝回答（类别：{category or '未说明'}）")
        if response.stop_reason == "max_tokens":
            raise LLMFormatError("Claude 的输出被截断了")
        if response.parsed_output is None:
            raise LLMFormatError(f"Claude 没有返回结构化结果（stop_reason={response.stop_reason}）")
        return response.parsed_output
