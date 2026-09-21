import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from agent.llm import LLMResponse
from agent.loop import (
    run_loop,
    BACKOFF_STEPS,
    ITERATION_LIMIT_MESSAGE,
    REPETITION_LIMIT_MESSAGE,
    TOKEN_LIMIT_MESSAGE,
)
from agent.tools.registry import Tool, ToolRegistry


def test_loop_answer_without_tools():
    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(return_value=LLMResponse(
        content="Labas! Kuo galiu padėti?",
        prompt_tokens=50,
        completion_tokens=20,
        total_tokens=70,
        cost_usd=0.00005,
        tool_calls=None,
    ))

    registry = ToolRegistry()
    messages = [{"role": "user", "content": "Labas"}]

    result = asyncio.run(run_loop(fake_llm, registry, messages))

    assert result.content == "Labas! Kuo galiu padėti?"
    assert result.iterations == 1
    assert result.naudotas_internetas is False
    assert result.total_tokens == 70
    assert result.stopped_by_limit is None


def test_loop_single_tool_call():
    tool_call_mock = {
        "id": "call_123",
        "type": "function",
        "function": {"name": "search_web", "arguments": '{"query": "Vilniaus orai"}'},
    }

    # Iteration 1: model calls tool; Iteration 2: model gives final answer
    resp1 = LLMResponse(
        content="",
        prompt_tokens=40,
        completion_tokens=10,
        total_tokens=50,
        cost_usd=0.00001,
        tool_calls=[tool_call_mock],
    )
    resp2 = LLMResponse(
        content="Šiandien Vilniuje saulėta.",
        prompt_tokens=80,
        completion_tokens=15,
        total_tokens=95,
        cost_usd=0.00002,
        tool_calls=None,
    )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=[resp1, resp2])

    registry = ToolRegistry()
    tool_called = []

    async def fake_search(query: str):
        tool_called.append(query)
        return f"Orai užklausai {query}: 20 C"

    registry.register(Tool(
        name="search_web",
        description="Paieška",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        risk="read_only",
        func=fake_search,
    ))

    result = asyncio.run(run_loop(fake_llm, registry, [{"role": "user", "content": "Kokie orai?"}]))

    assert result.content == "Šiandien Vilniuje saulėta."
    assert result.iterations == 2
    assert result.naudotas_internetas is True
    assert tool_called == ["Vilniaus orai"]
    assert result.total_tokens == 145


def test_loop_iteration_limit():
    step_counter = 0

    def generate_fn(*args, **kwargs):
        nonlocal step_counter
        step_counter += 1
        return LLMResponse(
            content="",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            cost_usd=0.00001,
            tool_calls=[{
                "id": f"call_{step_counter}",
                "type": "function",
                "function": {"name": "step_tool", "arguments": f'{{"i": {step_counter}}}'},
            }],
        )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=generate_fn)

    registry = ToolRegistry()
    registry.register(Tool(
        name="step_tool",
        description="Žingsnis",
        parameters={"type": "object", "properties": {"i": {"type": "integer"}}},
        risk="read_only",
        func=lambda i=0: f"Atlikta {i}",
    ))

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Ciklas"}],
        max_iterations=3,
    ))

    assert result.content == ITERATION_LIMIT_MESSAGE
    assert result.iterations == 3
    assert result.stopped_by_limit == "iterations"


def test_loop_repetition_detection():
    # Model returns identical tool call with same arguments twice in a row
    tool_call = {
        "id": "call_rep",
        "type": "function",
        "function": {"name": "search_web", "arguments": '{"query": "Lietuva"}'},
    }

    resp = LLMResponse(
        content="",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        cost_usd=0.00001,
        tool_calls=[tool_call],
    )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(return_value=resp)

    registry = ToolRegistry()
    registry.register(Tool(
        name="search_web",
        description="Paieška",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        risk="read_only",
        func=lambda query: "Rezultatas",
    ))

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Pakartok"}],
        max_iterations=10,
    ))

    assert result.content == REPETITION_LIMIT_MESSAGE
    assert result.stopped_by_limit == "repetition"


def test_loop_read_only_tool_retries_on_error():
    tool_call = {
        "id": "call_retry",
        "type": "function",
        "function": {"name": "read_tool", "arguments": "{}"},
    }

    resp1 = LLMResponse(
        content="",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        cost_usd=0.00001,
        tool_calls=[tool_call],
    )
    resp2 = LLMResponse(
        content="Pavyko po pakartojimo",
        prompt_tokens=30,
        completion_tokens=10,
        total_tokens=40,
        cost_usd=0.00001,
        tool_calls=None,
    )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=[resp1, resp2])

    attempts = 0

    def failing_read():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Laikina tinklo klaida")
        return "Sėkmė"

    registry = ToolRegistry()
    registry.register(Tool(
        name="read_tool",
        description="Skaitantis įrankis",
        parameters={"type": "object", "properties": {}},
        risk="read_only",
        func=failing_read,
    ))

    mock_sleep = AsyncMock()

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Skaityk"}],
        sleep_fn=mock_sleep,
    ))

    assert attempts == 3
    assert mock_sleep.await_count == 2
    assert result.content == "Pavyko po pakartojimo"


def test_loop_writing_tool_does_not_retry_on_error():
    tool_call = {
        "id": "call_write",
        "type": "function",
        "function": {"name": "create_event", "arguments": "{}"},
    }

    resp1 = LLMResponse(
        content="",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        cost_usd=0.00001,
        tool_calls=[tool_call],
    )
    resp2 = LLMResponse(
        content="Atsiprašome, sukurti nepavyko.",
        prompt_tokens=30,
        completion_tokens=10,
        total_tokens=40,
        cost_usd=0.00001,
        tool_calls=None,
    )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=[resp1, resp2])

    write_attempts = 0

    def failing_write():
        nonlocal write_attempts
        write_attempts += 1
        raise RuntimeError("Klaida kalendoriuje")

    registry = ToolRegistry()
    registry.register(Tool(
        name="create_event",
        description="Rašantis įrankis",
        parameters={"type": "object", "properties": {}},
        risk="destructive",
        func=failing_write,
    ))

    mock_sleep = AsyncMock()

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Sukurk"}],
        sleep_fn=mock_sleep,
    ))

    # Writing tool must be called exactly ONCE: no retries!
    assert write_attempts == 1
    assert mock_sleep.await_count == 0
    assert result.content == "Atsiprašome, sukurti nepavyko."


def test_loop_token_limit():
    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(return_value=LLMResponse(
        content="",
        prompt_tokens=40000,
        completion_tokens=30000,
        total_tokens=70000,
        cost_usd=0.01,
        tool_calls=[{
            "id": "call_tok",
            "type": "function",
            "function": {"name": "dummy", "arguments": "{}"},
        }],
    ))

    registry = ToolRegistry()
    registry.register(Tool(
        name="dummy",
        description="dummy",
        parameters={"type": "object", "properties": {}},
        risk="read_only",
        func=lambda: "ok",
    ))

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Test"}],
        max_tokens=60000,
    ))

    # Second iteration checks total_tokens >= 60000 and stops
    assert result.content == TOKEN_LIMIT_MESSAGE
    assert result.stopped_by_limit == "tokens"


def test_loop_duration_limit_stops_a_slow_model_call():
    """The duration budget must cut a model call, not wait for llm.py's own timeout."""
    async def slow_generate(messages, tools=None):
        await asyncio.sleep(5.0)
        raise AssertionError("modelio kvietimas turėjo būti nutrauktas")

    fake_llm = MagicMock()
    fake_llm.generate = slow_generate

    started = time.monotonic()
    result = asyncio.run(run_loop(
        fake_llm,
        ToolRegistry(),
        [{"role": "user", "content": "Klausimas"}],
        max_duration=0.3,
    ))
    elapsed = time.monotonic() - started

    assert result.stopped_by_limit == "duration"
    assert "trukmės riba" in result.content
    # Must return on its own budget, not after the model call finishes.
    assert elapsed < 2.0


def test_loop_read_only_tool_uses_all_backoff_steps_then_gives_up():
    """A read_only tool that always fails gets 4 attempts and 3 backoff waits."""
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_web", "arguments": '{"query": "x"}'},
    }
    resp1 = LLMResponse("", 10, 5, 15, 0.0, tool_calls=[tool_call])
    resp2 = LLMResponse("Nepavyko rasti.", 10, 5, 15, 0.0, tool_calls=None)

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=[resp1, resp2])

    attempts = {"n": 0}

    def always_fails(query: str = ""):
        attempts["n"] += 1
        raise RuntimeError("įrankis neveikia")

    registry = ToolRegistry()
    registry.register(Tool(
        name="search_web",
        description="Skaitantis įrankis",
        parameters={"type": "object", "properties": {}},
        risk="read_only",
        func=always_fails,
    ))

    delays: list[float] = []

    async def spy_sleep(d):
        delays.append(d)

    result = asyncio.run(run_loop(
        fake_llm,
        registry,
        [{"role": "user", "content": "Ieškok"}],
        sleep_fn=spy_sleep,
    ))

    # 3 kartojimai po pirmo bandymo, backoff 1 s, 2 s, 4 s.
    assert attempts["n"] == 4
    assert len(delays) == 3
    for step, actual in zip(BACKOFF_STEPS, delays):
        assert step <= actual < step + 0.5
    assert result.content == "Nepavyko rasti."
