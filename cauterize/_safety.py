from __future__ import annotations

import hashlib
import inspect
import threading
import time
from typing import Any

from . import _config


_attempt_counts: dict[str, tuple[int, float]] = {}   # key -> (count, last_time)
_attempt_lock = threading.Lock()
_function_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.Lock()

_ATTEMPT_TTL = 3600  # 1 hour

_INELIGIBLE_EXC_TYPES = frozenset({
    "KeyboardInterrupt", "SystemExit", "GeneratorExit",
    "MemoryError", "RecursionError", "SystemError",
    # I/O errors — cauterize can't fix missing files or broken connections
    "FileNotFoundError", "PermissionError", "ConnectionError",
    "TimeoutError", "OSError", "IOError", "BrokenPipeError",
})

_PROTECTED_MODULES = frozenset({
    "cauterize",    # self-guard
})


def is_eligible(func: Any, exc: BaseException) -> bool:
    # never heal explicitly protected functions
    if getattr(func, '__cauterize_protected__', False):
        return False

    if type(exc).__name__ in _INELIGIBLE_EXC_TYPES:
        return False

    # builtins and C extensions have no source
    if inspect.isbuiltin(func):
        return False

    try:
        inspect.getsource(func)
    except (OSError, TypeError):
        return False    # no source available — C extension or built-in

    # magic methods are framework internals
    name = getattr(func, "__name__", "")
    if name.startswith("__") and name.endswith("__"):
        return False

    # self-guard
    module = getattr(func, "__module__", "") or ""
    if any(module == m or module.startswith(m + ".") for m in _PROTECTED_MODULES):
        return False

    return True


def can_attempt(func: Any, exc: BaseException) -> bool:
    cfg = _config.get()
    key = _attempt_key(func, exc)

    with _attempt_lock:
        if key not in _attempt_counts:
            return True
        count, last_time = _attempt_counts[key]
        if time.time() - last_time > _ATTEMPT_TTL:
            del _attempt_counts[key]
            return True
        return count < cfg.max_retries


def record_attempt(func: Any, exc: BaseException) -> None:
    key = _attempt_key(func, exc)
    with _attempt_lock:
        count, _ = _attempt_counts.get(key, (0, 0.0))
        _attempt_counts[key] = (count + 1, time.time())


def get_lock(func: Any) -> threading.RLock:
    """Per-function RLock to prevent concurrent healing of the same function."""
    qualname = getattr(func, "__qualname__", str(id(func)))
    with _locks_lock:
        if qualname not in _function_locks:
            _function_locks[qualname] = threading.RLock()
        return _function_locks[qualname]


def _attempt_key(func: Any, exc: BaseException) -> str:
    qualname = getattr(func, "__qualname__", str(id(func)))
    exc_type = type(exc).__name__
    return hashlib.sha256(f"{qualname}:{exc_type}".encode()).hexdigest()[:16]
