from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from . import _config
from ._context import get_source


class AIResponse(BaseModel):
    fixed_source: str
    confidence: float
    explanation: str
    is_safe_to_auto_apply: bool
    safety_concerns: str


# Confidence discount factors by exception type
_EXC_DISCOUNTS: dict[str, float] = {
    "ValueError":      1.0,
    "TypeError":       1.0,
    "AttributeError":  0.9,
    "KeyError":        0.9,
    "IndexError":      0.9,
    "RuntimeError":    0.8,
    "Exception":       0.7,
}


def request_fix(func: Any, exc: BaseException, prompt: str) -> AIResponse | None:
    cfg = _config.get()
    agent: Agent[None, AIResponse] = Agent(
        model=cfg.model,
        result_type=AIResponse,
    )

    try:
        result = agent.run_sync(prompt)
        resp = result.data
        resp.confidence = _apply_discounts(resp, func, exc)
        return resp
    except Exception:
        return None


def _apply_discounts(resp: AIResponse, func: Any, exc: BaseException) -> float:
    c = resp.confidence

    # exception type
    c *= _EXC_DISCOUNTS.get(type(exc).__name__, 0.8)

    # vague exception message
    if len(str(exc)) < 10:
        c *= 0.7

    # safety concerns mentioned by AI
    if resp.safety_concerns:
        c *= 0.5

    # large diff
    orig_lines = len(get_source(func).splitlines())
    fixed_lines = len(resp.fixed_source.splitlines())
    if orig_lines > 0 and abs(fixed_lines - orig_lines) > 3:
        c *= 0.85

    return min(c, 1.0)
