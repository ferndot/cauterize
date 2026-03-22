from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AIResponse:
    fixed_source: str
    confidence: float
    explanation: str
    is_safe_to_auto_apply: bool
    safety_concerns: str


class Provider(ABC):
    @abstractmethod
    def request_fix(self, func: Any, exc: BaseException, prompt: str) -> AIResponse | None: ...
