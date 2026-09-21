"""ReAct agent loop for pi_agent.

Coordinates multi-step tool execution with strict limits:
- Max 10 iterations
- Max 120s duration
- Max 60 000 tokens per request across iterations
- Max 30s per tool call
- Repetition detection: same tool and arguments 2 times in a row
- Retries with backoff and jitter ONLY for model calls and read_only tools
- Execution state tracking for 'naudotas_internetas'
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agent.journal import record_journal_entry
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry, clean_tool_result

logger = logging.getLogger(__name__)

# Agent loop limits
MAX_ITERATIONS = 10
MAX_REQUEST_DURATION_SECONDS = 120.0
MAX_TOTAL_TOKENS = 60_000
TOOL_TIMEOUT_SECONDS = 30.0
# Retries after an error: backoff 1 s, 2 s, 4 s with random jitter.
# Three retries after the first attempt, so four attempts and three backoff steps.
MAX_RETRIES = 3
MAX_ATTEMPTS = MAX_RETRIES + 1
BACKOFF_STEPS = [1.0, 2.0, 4.0]
assert len(BACKOFF_STEPS) == MAX_RETRIES, "kiekvienam kartojimui reikia savo backoff pakopos"

# Lithuanian limit messages
ITERATION_LIMIT_MESSAGE = (
    "Atsiprašome, pasiekta užklausos iteracijų riba (10). Veiksmas buvo sustabdytas."
)
DURATION_LIMIT_MESSAGE = (
    "Atsiprašome, viršyta užklausos vykdymo trukmės riba (120 s). Veiksmas buvo sustabdytas."
)
TOKEN_LIMIT_MESSAGE = (
    "Atsiprašome, viršyta užklausos žetonų riba (60 000). Veiksmas buvo sustabdytas."
)
REPETITION_LIMIT_MESSAGE = (
    "Atsiprašome, aptiktas pasikartojantis įrankio kvietimas. Veiksmas buvo sustabdytas, "
    "kad būtų išvengta pasikartojimų."
)


@dataclass
class LoopResult:
    """Result of running the ReAct loop."""

    content: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    iterations: int = 0
    naudotas_internetas: bool = False
    stopped_by_limit: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)


def _remaining(start_time: float, max_duration: float) -> float:
    """Returns the seconds left in the request budget."""
    return max_duration - (time.monotonic() - start_time)


def _parse_tool_call(tc: Any) -> tuple[str, str, dict[str, Any], str]:
    """Extracts (call_id, function_name, parsed_args_dict, raw_args_str) from tool call."""
    if isinstance(tc, dict):
        call_id = str(tc.get("id", "call_1"))
        fn_data = tc.get("function", {})
        if isinstance(fn_data, dict):
            name = str(fn_data.get("name", ""))
            args_raw = fn_data.get("arguments", "{}")
        else:
            name = str(getattr(fn_data, "name", ""))
            args_raw = getattr(fn_data, "arguments", "{}")
    else:
        call_id = str(getattr(tc, "id", "call_1"))
        fn = getattr(tc, "function", None)
        name = str(getattr(fn, "name", ""))
        args_raw = getattr(fn, "arguments", "{}")

    if isinstance(args_raw, dict):
        args_dict = args_raw
        raw_str = json.dumps(args_dict, sort_keys=True)
    else:
        raw_str = str(args_raw)
        try:
            args_dict = json.loads(args_raw) if args_raw else {}
            if not isinstance(args_dict, dict):
                args_dict = {"_value": args_dict}
        except Exception:
            args_dict = {}

    return call_id, name, args_dict, raw_str


async def run_loop(
    llm_client: LLMClient,
    tool_registry: ToolRegistry,
    messages: list[dict[str, Any]],
    user_id: int = 0,
    run_id: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
    max_duration: float = MAX_REQUEST_DURATION_SECONDS,
    max_tokens: int = MAX_TOTAL_TOKENS,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    approval_hook: Callable[..., Awaitable[str | None]] | None = None,
) -> LoopResult:
    """Executes ReAct reasoning loop until final answer or limit reached."""
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex[:12]}"

    start_time = time.monotonic()
    loop_messages = list(messages)

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0
    naudotas_internetas = False

    last_call_signature: tuple[str, str] | None = None
    schemas = tool_registry.get_schemas()

    for iteration in range(1, max_iterations + 1):
        # 1. Check time limit
        if time.monotonic() - start_time >= max_duration:
            return LoopResult(
                content=DURATION_LIMIT_MESSAGE,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                iterations=iteration - 1,
                naudotas_internetas=naudotas_internetas,
                stopped_by_limit="duration",
                messages=loop_messages,
            )

        # 2. Check token limit
        if total_tokens >= max_tokens:
            return LoopResult(
                content=TOKEN_LIMIT_MESSAGE,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                iterations=iteration - 1,
                naudotas_internetas=naudotas_internetas,
                stopped_by_limit="tokens",
                messages=loop_messages,
            )

        # 3. Call LLM with retries and backoff
        llm_resp = None
        for attempt in range(MAX_ATTEMPTS):
            remaining = _remaining(start_time, max_duration)
            if remaining <= 0:
                break
            try:
                llm_resp = await asyncio.wait_for(
                    llm_client.generate(messages=loop_messages, tools=schemas),
                    timeout=remaining,
                )
                break
            except asyncio.TimeoutError:
                logger.warning("Modelio kvietimas nutrauktas pasibaigus užklausos trukmės biudžetui")
                llm_resp = None
                break
            except Exception as e:
                logger.warning("Klaida kviečiant modelį (bandymas %d/%d): %s", attempt + 1, MAX_ATTEMPTS, e)
                # No backoff step exists after the last retry.
                delay = (
                    BACKOFF_STEPS[attempt] + random.uniform(0.0, 0.5)
                    if attempt < MAX_RETRIES
                    else None
                )
                if delay is not None and _remaining(start_time, max_duration) > delay:
                    await sleep_fn(delay)
                else:
                    return LoopResult(
                        content=f"Atsiprašome, įvyko klaida kreipiantis į modelį: {e}",
                        total_prompt_tokens=total_prompt_tokens,
                        total_completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        total_cost_usd=total_cost,
                        iterations=iteration,
                        naudotas_internetas=naudotas_internetas,
                        stopped_by_limit="model_error",
                        messages=loop_messages,
                    )

        if llm_resp is None:
            return LoopResult(
                content=DURATION_LIMIT_MESSAGE,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                iterations=iteration - 1,
                naudotas_internetas=naudotas_internetas,
                stopped_by_limit="duration",
                messages=loop_messages,
            )

        total_prompt_tokens += llm_resp.prompt_tokens
        total_completion_tokens += llm_resp.completion_tokens
        total_tokens += llm_resp.total_tokens
        total_cost += llm_resp.cost_usd

        try:
            record_journal_entry(
                run_id=run_id,
                user_id=user_id,
                tool_name=None,
                tokens=llm_resp.total_tokens,
                cost_usd=llm_resp.cost_usd,
                prompt_tokens=llm_resp.prompt_tokens,
                completion_tokens=llm_resp.completion_tokens,
            )
        except Exception as log_err:
            logger.warning("Klaida rašant modelio kvietimą į žurnalą: %s", log_err)

        # If no tool calls, return final response
        if not llm_resp.tool_calls:
            return LoopResult(
                content=llm_resp.content,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                iterations=iteration,
                naudotas_internetas=naudotas_internetas,
                messages=loop_messages,
            )

        # 4. Process tool calls
        formatted_tool_calls: list[dict[str, Any]] = []
        parsed_calls: list[tuple[str, str, dict[str, Any], str]] = []

        for tc in llm_resp.tool_calls:
            call_id, name, args_dict, raw_str = _parse_tool_call(tc)
            formatted_tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_str,
                },
            })
            parsed_calls.append((call_id, name, args_dict, raw_str))

        # Add assistant message with tool calls to history
        loop_messages.append({
            "role": "assistant",
            "content": llm_resp.content or None,
            "tool_calls": formatted_tool_calls,
        })

        # Execute each tool call
        for call_id, name, args_dict, _ in parsed_calls:
            normalized_args = json.dumps(args_dict, sort_keys=True)
            current_signature = (name, normalized_args)

            # Repetition detection: 2 consecutive calls with same tool and same args
            if current_signature == last_call_signature:
                logger.warning("Aptiktas pasikartojantis įrankio kvietimas: %s", current_signature)
                return LoopResult(
                    content=REPETITION_LIMIT_MESSAGE,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    iterations=iteration,
                    naudotas_internetas=naudotas_internetas,
                    stopped_by_limit="repetition",
                    messages=loop_messages,
                )

            last_call_signature = current_signature

            tool_remaining = _remaining(start_time, max_duration)
            if tool_remaining <= 0:
                return LoopResult(
                    content=DURATION_LIMIT_MESSAGE,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    iterations=iteration,
                    naudotas_internetas=naudotas_internetas,
                    stopped_by_limit="duration",
                    messages=loop_messages,
                )

            try:
                record_journal_entry(
                    run_id=run_id,
                    user_id=user_id,
                    tool_name=name,
                    tokens=0,
                    cost_usd=0.0,
                    details={"arguments": args_dict},
                )
            except Exception as log_err:
                logger.warning("Klaida rašant įrankio kvietimą į žurnalą: %s", log_err)

            if name == "search_web":
                naudotas_internetas = True

            tool = tool_registry.get(name)
            is_read_only = (tool.risk == "read_only") if tool else True

            # Rašantys veiksmai gali reikalauti vartotojo patvirtinimo. Tada
            # įrankis NEVYKDOMAS, o modeliui grąžinamas paaiškinimas.
            if approval_hook is not None:
                pending_note = await approval_hook(
                    name,
                    args_dict,
                    tool.risk if tool else "read_only",
                    naudotas_internetas,
                )
                if pending_note is not None:
                    loop_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": clean_tool_result(pending_note, tool_name=name),
                    })
                    continue

            result_str = ""
            if is_read_only:
                # Retries allowed for read_only tools
                for attempt in range(MAX_ATTEMPTS):
                    attempt_remaining = _remaining(start_time, max_duration)
                    if attempt_remaining <= 0:
                        result_str = clean_tool_result(
                            f"Klaida vykdant įrankį '{name}': baigėsi užklausos laiko biudžetas.",
                            tool_name=name,
                        )
                        break
                    try:
                        result_str = await asyncio.wait_for(
                            tool_registry.execute(name, args_dict, raise_on_error=True),
                            timeout=min(TOOL_TIMEOUT_SECONDS, attempt_remaining),
                        )
                        break
                    except Exception as err:
                        logger.warning(
                            "Klaida vykdant read_only įrankį '%s' (bandymas %d/%d): %s",
                            name,
                            attempt + 1,
                            MAX_ATTEMPTS,
                            err,
                        )
                        delay = (
                            BACKOFF_STEPS[attempt] + random.uniform(0.0, 0.5)
                            if attempt < MAX_RETRIES
                            else None
                        )
                        if delay is not None and _remaining(start_time, max_duration) > delay:
                            await sleep_fn(delay)
                        else:
                            result_str = clean_tool_result(
                                f"Klaida vykdant įrankį '{name}': {err}",
                                tool_name=name,
                            )
                            break
            else:
                # Write/destructive tools: NO retries
                try:
                    result_str = await asyncio.wait_for(
                        tool_registry.execute(name, args_dict, raise_on_error=True),
                        timeout=min(TOOL_TIMEOUT_SECONDS, tool_remaining),
                    )
                except Exception as err:
                    logger.warning("Klaida vykdant rašantį įrankį '%s' (be pakartojimo): %s", name, err)
                    result_str = clean_tool_result(
                        f"Klaida vykdant įrankį '{name}': {err}",
                        tool_name=name,
                    )

            loop_messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": result_str,
            })

    # Reached maximum iterations without final text answer
    return LoopResult(
        content=ITERATION_LIMIT_MESSAGE,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        iterations=max_iterations,
        naudotas_internetas=naudotas_internetas,
        stopped_by_limit="iterations",
        messages=loop_messages,
    )
