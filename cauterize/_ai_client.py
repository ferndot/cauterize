from __future__ import annotations

import json
from typing import Any

import anthropic

from . import _config
from ._context import get_source

_CLIENT: anthropic.Anthropic | None = None

_TOOL = {
    "name": "submit_fix",
    "description": "Submit a corrected version of the function with triage scoring.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fixed_source": {
                "type": "string",
                "description": "Complete corrected function source, including the def line.",
            },
            "confidence_score": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
                "description": (
                    "How confident you are in the fix. "
                    "1=High: root cause unambiguous, fix well-understood, suitable for automated apply. "
                    "2=Good: root cause clear, minor edge-case ambiguity, light review sufficient. "
                    "3=Moderate: root cause likely correct but assumptions made, engineer should verify. "
                    "4=Low: multiple possible root causes or complex path, deep-dive needed. "
                    "5=Very Low: uncertain, could not fully trace, may require reproduction."
                ),
            },
            "risk_score": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
                "description": (
                    "Potential impact of applying this patch. "
                    "1=XS: cosmetic or low-traffic path. "
                    "2=S: minor feature impact, workaround exists. "
                    "3=M: affects a common workflow or customer segment. "
                    "4=L: affects core workflow, high-value customers, or data integrity. "
                    "5=XL: platform-wide impact, security risk, data loss, or revenue-affecting."
                ),
            },
            "complexity_score": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5],
                "description": (
                    "Difficulty of the change. "
                    "1=XS: 1-2 line fix, single code path. "
                    "2=S: small change, clear scope, standard testing. "
                    "3=M: moderate change, requires thoughtful testing. "
                    "4=L: larger change, multi-file, careful coordination needed. "
                    "5=XL: major change, multi-domain, possible migration."
                ),
            },
            "explanation": {
                "type": "string",
                "description": "One-sentence explanation of the bug and the fix.",
            },
            "is_safe_to_auto_apply": {
                "type": "boolean",
                "description": "True if safe to hot-patch without human review (confidence_score ≤ 2 and risk_score ≤ 3).",
            },
            "safety_concerns": {
                "type": "string",
                "description": "Any safety or correctness concerns; empty string if none.",
            },
        },
        "required": [
            "fixed_source", "confidence_score", "risk_score", "complexity_score",
            "explanation", "is_safe_to_auto_apply", "safety_concerns",
        ],
    },
}

# Map 1-5 confidence score to internal 0-1 float
# 1=High → 0.95, 2=Good → 0.80, 3=Moderate → 0.60, 4=Low → 0.35, 5=Very Low → 0.15
_CONFIDENCE_MAP: dict[int, float] = {1: 0.95, 2: 0.80, 3: 0.60, 4: 0.35, 5: 0.15}

# Risk discount: high risk reduces willingness to auto-apply
_RISK_DISCOUNTS: dict[int, float] = {1: 1.0, 2: 1.0, 3: 0.95, 4: 0.70, 5: 0.40}

# Complexity discount: highly complex patches need more confidence
_COMPLEXITY_DISCOUNTS: dict[int, float] = {1: 1.0, 2: 1.0, 3: 0.90, 4: 0.80, 5: 0.65}


class AIResponse:
    __slots__ = (
        "fixed_source", "confidence", "confidence_score", "risk_score",
        "complexity_score", "explanation", "is_safe_to_auto_apply", "safety_concerns",
    )

    def __init__(
        self,
        fixed_source: str,
        confidence: float,
        confidence_score: int,
        risk_score: int,
        complexity_score: int,
        explanation: str,
        is_safe_to_auto_apply: bool,
        safety_concerns: str,
    ) -> None:
        self.fixed_source = fixed_source
        self.confidence = confidence             # internal 0-1 float (after discounts)
        self.confidence_score = confidence_score # LLM-reported 1-5
        self.risk_score = risk_score             # LLM-reported 1-5
        self.complexity_score = complexity_score # LLM-reported 1-5
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
                confidence_score = int(data.get("confidence_score", 3))
                risk_score = int(data.get("risk_score", 3))
                complexity_score = int(data.get("complexity_score", 2))
                ai_resp = AIResponse(
                    fixed_source=data["fixed_source"],
                    confidence=0.0,  # computed below
                    confidence_score=confidence_score,
                    risk_score=risk_score,
                    complexity_score=complexity_score,
                    explanation=data.get("explanation", ""),
                    is_safe_to_auto_apply=bool(data.get("is_safe_to_auto_apply", False)),
                    safety_concerns=data.get("safety_concerns", ""),
                )
                ai_resp.confidence = _compute_confidence(ai_resp, func, exc)
                return ai_resp
    except Exception:  # noqa: BLE001
        return None
    return None


def _compute_confidence(resp: AIResponse, func: Any, exc: BaseException) -> float:
    """
    Compute internal 0-1 confidence from the 1-5 scoring dimensions.

    Discounts applied in order:
      1. Confidence score (1-5) → base float via _CONFIDENCE_MAP
      2. Risk score multiplier
      3. Complexity score multiplier
      4. Safety concerns → 0.5× if any
    """
    c = _CONFIDENCE_MAP.get(resp.confidence_score, 0.60)
    c *= _RISK_DISCOUNTS.get(resp.risk_score, 0.80)
    c *= _COMPLEXITY_DISCOUNTS.get(resp.complexity_score, 0.90)
    if resp.safety_concerns:
        c *= 0.5
    return min(c, 1.0)
