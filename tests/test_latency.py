"""Tests for response-speed settings: OpenRouter routing and the typing indicator."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import agent.telegram_ui as telegram_ui
from agent.llm import LLMClient


def test_generate_requests_fast_provider_with_reasoning():
    client = LLMClient(api_key="test-key")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Labas", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
    )
    create = AsyncMock(return_value=response)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    asyncio.run(client.generate(messages=[{"role": "user", "content": "Labas"}]))

    extra_body = create.call_args.kwargs["extra_body"]
    assert extra_body["provider"]["sort"] == "latency"
    assert extra_body["provider"]["max_price"] == {"prompt": 0.30, "completion": 1.20}
    # Reasoning is left to the model's default, which is on.
    assert "reasoning" not in extra_body


def test_keep_typing_repeats_until_block_ends(monkeypatch):
    monkeypatch.setattr(telegram_ui, "TYPING_INTERVAL_SECONDS", 0.01)
    chat = SimpleNamespace(send_action=AsyncMock())

    async def run() -> None:
        async with telegram_ui.keep_typing(chat):
            await asyncio.sleep(0.05)
        sent = chat.send_action.await_count
        await asyncio.sleep(0.03)
        # The indicator stops once the answer is ready.
        assert chat.send_action.await_count == sent

    asyncio.run(run())
    assert chat.send_action.await_count >= 3


def test_keep_typing_survives_telegram_errors(monkeypatch):
    monkeypatch.setattr(telegram_ui, "TYPING_INTERVAL_SECONDS", 0.01)
    chat = SimpleNamespace(send_action=AsyncMock(side_effect=RuntimeError("network")))

    async def run() -> str:
        async with telegram_ui.keep_typing(chat):
            await asyncio.sleep(0.03)
        return "atsakymas"

    assert asyncio.run(run()) == "atsakymas"
