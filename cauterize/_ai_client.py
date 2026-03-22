from __future__ import annotations

import json
from typing import Any

import anthropic

from . import _config
from ._context import get_source

_CLIENT: anthropic.Anthropic | None = None

_TOOL = {
    "name": "submit_fix",
    "description": "Submit a corrected version of the function.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fixed_source": {
                "type": "string",
                "description": "Complete corrected function source, including the def line.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0-1.0 that this fix is correct.",
            },
            "explanation": {
                "type": "string",
                "description": "One-sentence explanation of the bug and the fix.",
            },
            "is_safe_to_auto_apply": {
                "type": "boolean",
                "description": "True if safe to hot-patch without human review.",
            },
            "safety_concerns": {
                "type": "string",
                "description": "Any safety or correctness concerns; empty string if none.",
            },
        },
        "required": ["fixed_source", "confidence", "explanation", "is_safe_to_auto_apply", "safety_concerns"],
    },
}

# Confidence discount factors by exception type
_EXC_DISCOUNTS: dict[str, float] = {
    "ValueError":     1.0,
    "TypeError":      1.0,
    "AttributeError": 0.9,
    "KeyError":       0.9,
    "IndexError":     0.9,
    "RuntimeError":   0.8,
    "Exception":      0.7,
}


class AIResponse:
    __slots__ = ("fixed_source", "confidence", "explanation", "is_safe_to_auto_apply", "safety_concerns")

    def __init__(self, fixed_source: str, confidence: float, explanation: str,
                 is_safe_to_auto_apply: bool, safety_concerns: str) -> None:
        self.fixed_source = fixed_source
        self.confidence = confidence
        self.explanation = explanation
        self.is_safe_to_auto_apply = is_safe_to_auto_apply
        self.safety_concerns = safety_concerns


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic()
    return _CLIENT


def request_fix(func: Any, exc: BaseException, prompt: str) -> AIResponse | None:
    cfg = _config.get()
    try:
        resp = _client().messages.create(
            model=cfg.model,
            max_tokens=2048,
            tools=[_TOOL],  # type: ignore[list-item]
            tool_choice={"type": "tool", "name": "submit_fix"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_fix":
                data = block.input if isinstance(block.input, dict) else json.loads(block.input)
                ai_resp = AIResponse(
                    fixed_source=data["fixed_source"],
                    confidence=float(data.get("confidence", 0.0)),
                    explanation=data.get("explanation", ""),
                    is_safe_to_auto_apply=bool(data.get("is_safe_to_auto_apply", False)),
                    safety_concerns=data.get("safety_concerns", ""),
                )
                ai_resp.confidence = _apply_discounts(ai_resp, func, exc)
                return ai_resp
    except Exception:  # noqa: BLE001
        return None
    return None


def _apply_discounts(resp: AIResponse, func: Any, exc: BaseException) -> float:
    c = resp.confidence
    c *= _EXC_DISCOUNTS.get(type(exc).__name__, 0.8)
    if len(str(exc)) < 10:
        c *= 0.7
    if resp.safety_concerns:
        c *= 0.5
    orig_lines = len(get_source(func).splitlines())
    fixed_lines = len(resp.fixed_source.splitlines())
    if orig_lines > 0 and abs(fixed_lines - orig_lines) > 3:
        c *= 0.85
    return min(c, 1.0)
