from __future__ import annotations

import inspect
import linecache
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any


@dataclass
class FrameInfo:
    filename: str
    lineno: int
    func_name: str
    source: str | None
    locals: dict[str, str]      # name -> "type:repr"


@dataclass
class ExceptionContext:
    exc_type: str
    exc_message: str
    frames: list[FrameInfo]
    target_frame: FrameInfo | None
    target_func: Any            # the actual function object


@dataclass
class HealContext:
    """Produced after a successful heal. Passed to notifiers and Jira."""
    func_qualname: str
    exc_type: str
    exc_message: str
    fixed_source: str
    explanation: str
    confidence: float
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )


def extract(exc: BaseException, func: Any = None) -> ExceptionContext:
    frames = _walk_frames(exc.__traceback__)
    return ExceptionContext(
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        frames=frames,
        target_frame=frames[-1] if frames else None,
        target_func=func,
    )


def _walk_frames(tb: TracebackType | None) -> list[FrameInfo]:
    frames = []
    while tb is not None:
        frame = tb.tb_frame
        is_last = tb.tb_next is None
        frames.append(FrameInfo(
            filename=frame.f_code.co_filename,
            lineno=tb.tb_lineno,
            func_name=frame.f_code.co_name,
            source=_get_source(frame) if is_last else None,
            locals=_extract_locals(frame.f_locals) if is_last else {},
        ))
        tb = tb.tb_next
    return frames


def _get_source(frame) -> str | None:
    try:
        return inspect.getsource(frame.f_code)
    except (OSError, TypeError):
        pass
    lines = linecache.getlines(frame.f_code.co_filename)
    return "".join(lines) if lines else None


def _extract_locals(f_locals: dict) -> dict[str, str]:
    result = {}
    for name, value in f_locals.items():
        if name.startswith("__"):
            continue
        try:
            result[name] = f"{type(value).__name__}:{repr(value)[:100]}"
        except Exception:
            result[name] = f"{type(value).__name__}:<repr failed>"
    return result


def get_source(func: Any) -> str:
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ""
