from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anthropic

from . import _config
from ._context import get_source


@dataclass
class AIResponse:
    fixed_source: str
    confidence: float
    explanation: str
    is_safe_to_auto_apply: bool
    safety_concerns: str


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "fixed_source":          {"type": "string"},
        "confidence":            {"type": "number"},
        "explanation":           {"type": "string"},
        "is_safe_to_auto_apply": {"type": "boolean"},
        "safety_concerns":       {"type": "string"},
    },
    "required": ["fixed_source", "confidence", "explanation",
                 "is_safe_to_auto_apply", "safety_concerns"],
}

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
    client = anthropic.Anthropic()

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=cfg.model,
                max_tokens=2048,
                tools=[{
                    "name": "submit_fix",
                    "description": "Submit the fixed function source and metadata",
                    "input_schema": _RESPONSE_SCHEMA,
                }],
                tool_choice={"type": "tool", "name": "submit_fix"},
                messages=[{"role": "user", "content": prompt}],
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_fix":
                    resp = AIResponse(**block.input)
                    resp.confidence = _apply_discounts(resp, func, exc)
                    return resp

            return None

        except anthropic.RateLimitError:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return None
        except anthropic.APIError:
            return None

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
