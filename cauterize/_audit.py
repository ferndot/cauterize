from __future__ import annotations

import json
import os
import time
from typing import Any

from . import _config


def write(
    ctx: Any,
    outcome: str,
    confidence: float,
    attempt: int,
    explanation: str = "",
) -> None:
    """outcome: 'healed' | 'rejected' | 'failed'"""
    _write_record({
        "timestamp": _now(),
        "func": _func_name(ctx),
        "exc_type": getattr(ctx, "exc_type", ""),
        "confidence": round(confidence, 4),
        "outcome": outcome,
        "attempt": attempt,
        "explanation": explanation,
    })


def write_jira_failure(ctx: Any, exc: Exception) -> None:
    _write_record({
        "timestamp": _now(),
        "event": "jira_failure",
        "func": _func_name(ctx),
        "error": str(exc),
    })


def _write_record(record: dict) -> None:
    path = _config.get().audit_path
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass    # audit must never affect the application


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _func_name(ctx: Any) -> str:
    func = getattr(ctx, "target_func", None)
    if func is not None:
        return getattr(func, "__qualname__", repr(func))
    return getattr(ctx, "func_qualname", "<unknown>")
