from __future__ import annotations

import json
import time
from typing import Any

from .._provider import AIResponse, Provider
from .._context import get_source

_EXC_DISCOUNTS: dict[str, float] = {
    "ValueError":      1.0,
    "TypeError":       1.0,
    "AttributeError":  0.9,
    "KeyError":        0.9,
    "IndexError":      0.9,
    "RuntimeError":    0.8,
    "Exception":       0.7,
}

_TOOL_PARAMETERS = {
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


class OpenAIProvider(Provider):
    def __init__(self, model: str = "gpt-4o", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key

    def request_fix(self, func: Any, exc: BaseException, prompt: str) -> AIResponse | None:
        try:
            import openai as _openai
        except ImportError:
            raise ImportError(
                "openai package is required to use OpenAIProvider. "
                "Install it with: pip install cauterize[openai]"
            )

        client = _openai.OpenAI(api_key=self._api_key) if self._api_key else _openai.OpenAI()

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    max_tokens=2048,
                    tools=[{
                        "type": "function",
                        "function": {
                            "name": "submit_fix",
                            "description": "Submit the fixed function source and metadata",
                            "parameters": _TOOL_PARAMETERS,
                        },
                    }],
                    tool_choice={"type": "function", "function": {"name": "submit_fix"}},
                    messages=[{"role": "user", "content": prompt}],
                )

                for choice in response.choices:
                    if choice.message.tool_calls:
                        for tc in choice.message.tool_calls:
                            if tc.function.name == "submit_fix":
                                data = json.loads(tc.function.arguments)
                                resp = AIResponse(**data)
                                resp.confidence = _apply_discounts(resp, func, exc)
                                return resp

                return None

            except Exception as e:
                cls_name = type(e).__name__
                if "RateLimitError" in cls_name:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    else:
                        return None
                else:
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
