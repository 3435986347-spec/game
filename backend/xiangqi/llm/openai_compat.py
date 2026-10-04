"""OpenAI 兼容接口的适配（DeepSeek 以及其他兼容服务，包括本地部署的模型）：openai SDK + JSON 模式。

JSON 模式只保证输出是 JSON，不保证字段齐全，所以结果一律再用 pydantic 校验。
"""

from __future__ import annotations

import os

import openai
from pydantic import ValidationError

from .base import LLMError, LLMFormatError, OpenAICompatSettings, T, extract_json


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, settings: OpenAICompatSettings, *, timeout: float = 90.0) -> None:
        self.settings = settings
        self.model = settings.model
        self.timeout = timeout
        self._client: openai.AsyncOpenAI | None = None

    @property
    def has_key(self) -> bool:
        return bool(os.environ.get(self.settings.api_key_env))

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            key = os.environ.get(self.settings.api_key_env)
            if not key:
                raise LLMError(
                    f"没有找到 API Key：请设置环境变量 {self.settings.api_key_env}"
                    "（可以写在仓库根目录的 .env 文件里）"
                )
            self._client = openai.AsyncOpenAI(
                api_key=key, base_url=self.settings.base_url, timeout=self.timeout
            )
        return self._client

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        client = self._get_client()
        extra = {"response_format": {"type": "json_object"}} if self.settings.json_mode else {}
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **extra,
            )
        except openai.AuthenticationError as e:
            raise LLMError(f"API Key 无效（{e.message}）") from e
        except openai.NotFoundError as e:
            raise LLMError(f"找不到模型 {self.model}，请检查 config.toml（{e.message}）") from e
        except openai.RateLimitError as e:
            raise LLMError("请求太频繁或额度不足，请稍后再试") from e
        except openai.APIStatusError as e:
            raise LLMError(f"服务返回错误 {e.status_code}：{e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"无法连接 {self.settings.base_url}（网络问题或超时）：{e}") from e
        except openai.OpenAIError as e:
            raise LLMError(f"调用大模型失败：{e}") from e

        if not response.choices:
            raise LLMFormatError("大模型没有返回内容")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMFormatError("大模型的输出被截断了")
        text = choice.message.content or ""
        try:
            return schema.model_validate_json(extract_json(text))
        except ValidationError as e:
            raise LLMFormatError(f"大模型输出的 JSON 缺少字段或类型不对：{e}") from e
