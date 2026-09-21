"""OpenRouter LLM client for pi_agent.

Calls deepseek/deepseek-v4.1-flash and tracks token usage and costs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from openai import AsyncOpenAI
from agent.db import calculate_cost
from agent.prompts import SUMMARY_SYSTEM_PROMPT, build_summary_prompt

# Model and request settings
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_TOKENS = 1500
REQUEST_TIMEOUT = 120.0


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    tool_calls: list[Any] | None = None
    raw_response: Any = None


class LLMClient:
    """Client for OpenRouter API interactions."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model
        self.base_url = base_url
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=REQUEST_TIMEOUT,
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Sends messages to the model and returns content with token/cost usage and tool calls."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = getattr(choice.message, "tool_calls", None)

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else (prompt_tokens + completion_tokens)

        cost_usd = calculate_cost(prompt_tokens, completion_tokens)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
            raw_response=response,
        )

    async def summarize(
        self,
        messages: list[dict],
        existing_summary: str | None = None,
    ) -> LLMResponse:
        """Summarizes older conversation messages into a single Lithuanian summary.

        Returns the full LLMResponse so the caller can record the cost.
        """
        return await self.generate(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": build_summary_prompt(messages, existing_summary)},
            ]
        )
