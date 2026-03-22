from __future__ import annotations

from typing import Any

from . import _config
from ._provider import AIResponse


def request_fix(func: Any, exc: BaseException, prompt: str) -> AIResponse | None:
    """Delegate to the configured provider. Kept as a module-level function for backward compatibility."""
    cfg = _config.get()
    return cfg.provider.request_fix(func, exc, prompt)
