"""
Agentic escalation path via the Claude Agent SDK.

Fires in a background daemon thread when the hot-path (direct API call) exhausts
its retries without producing a valid patch. The agent gets read-only access to the
codebase so it can understand surrounding context before generating a fix.

Requires: pip install cauterize[agent]
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from pathlib import Path
from typing import Any

from . import _audit, _config, _patcher, _validator
from ._context import ExceptionContext, get_source

log = logging.getLogger("cauterize.escalation")


def escalate(func: Any, ctx: ExceptionContext) -> None:
    """Fire background agentic escalation. Non-blocking — returns immediately."""
    log.info("escalation: launching agentic session for %s", func.__qualname__)
    threading.Thread(
        target=_run,
        args=(func, ctx),
        daemon=True,
    ).start()


# ── internal ───────────────────────────────────────────────────────────────────

def _run(func: Any, ctx: ExceptionContext) -> None:
    try:
        asyncio.run(_agentic_fix(func, ctx))
    except Exception:
        pass


async def _agentic_fix(func: Any, ctx: ExceptionContext) -> None:
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
    except ImportError:
        return  # optional dep not installed — silent no-op

    source = get_source(func)
    if not source:
        return

    try:
        module_file = inspect.getfile(func)
    except (TypeError, OSError):
        return

    cwd = str(Path(module_file).parent)
    prompt = _build_prompt(func, source, ctx, module_file)

    result_text = None
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=cwd,
            allowed_tools=["Read", "Grep", "Glob"],
            permission_mode="default",
            max_turns=10,
            system_prompt=(
                "You are a Python debugging expert with read-only access to the codebase. "
                "Use the available tools to understand context, then return the corrected "
                "function source wrapped between <cauterize_fix> and </cauterize_fix> tags, "
                "followed by a line starting with 'Explanation: ' describing the fix. "
                "Output nothing else after the explanation line."
            ),
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result

    if not result_text:
        return

    fixed_source = _parse_fix(result_text)
    if not fixed_source:
        log.warning("escalation: agent returned no parseable fix for %s", func.__qualname__)
        return

    explanation = _parse_explanation(result_text)

    log.info(
        "escalation: agent generated patch for %s\n--- patch ---\n%s\n--- end patch ---",
        func.__qualname__, fixed_source,
    )

    original_source = get_source(func)
    err = _validator.validate(original_source, fixed_source, func)
    if err:
        log.warning("escalation: validation failed for %s — %s", func.__qualname__, err)
        _audit.write(ctx, "escalation_rejected", 1.0, 0, f"validation: {err}")
        return

    new_func = _patcher.compile_function(fixed_source, func)
    if new_func is None:
        log.warning("escalation: compile failed for %s", func.__qualname__)
        return

    cfg = _config.get()
    if not cfg.dry_run:
        snap = _patcher.snapshot(func)
        _patcher.apply(snap, new_func)

    log.info("escalation: patch applied to %s — %s", func.__qualname__, explanation)
    _audit.write(ctx, "escalation_healed", 1.0, 0, explanation)


def _build_prompt(func: Any, source: str, ctx: ExceptionContext, module_file: str) -> str:
    traceback_lines = []
    for frame in ctx.frames:
        traceback_lines.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.func_name}')
        if frame.source:
            # Show the specific failing line only
            lines = frame.source.splitlines()
            if 0 < frame.lineno <= len(lines):
                traceback_lines.append(f"    {lines[frame.lineno - 1].strip()}")
    traceback_str = "\n".join(traceback_lines)

    return (
        f"Fix the following Python function. "
        f"It raised `{ctx.exc_type}: {ctx.exc_message}`\n\n"
        f"Function source (from {module_file}):\n"
        f"```python\n{source}\n```\n\n"
        f"Traceback:\n{traceback_str}\n\n"
        f"Use the available tools to read surrounding files for context if needed. "
        f"Return the corrected function between <cauterize_fix> and </cauterize_fix> tags, "
        f"then a line: 'Explanation: <one sentence describing the fix>'"
    )


def _parse_fix(text: str) -> str | None:
    start = text.find("<cauterize_fix>")
    end = text.find("</cauterize_fix>")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start + len("<cauterize_fix>"):end].strip()


def _parse_explanation(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Explanation:"):
            return line[len("Explanation:"):].strip()
    return "agentic escalation fix"
