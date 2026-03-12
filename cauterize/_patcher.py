from __future__ import annotations

import functools
import sys
import textwrap
from dataclasses import dataclass
from typing import Any


@dataclass
class Snapshot:
    original_func: Any
    module_name: str
    func_name: str


def snapshot(func: Any) -> Snapshot:
    return Snapshot(
        original_func=func,
        module_name=getattr(func, "__module__", "") or "",
        func_name=getattr(func, "__name__", ""),
    )


def compile_function(fixed_source: str, original_func: Any) -> Any | None:
    """Compile fixed_source into a callable using the original module's namespace."""
    try:
        source = textwrap.dedent(fixed_source)
        module_name = getattr(original_func, "__module__", None)
        module = sys.modules.get(module_name) if module_name else None
        global_ns = vars(module).copy() if module else {}

        code = compile(source, f"<cauterize:{getattr(original_func, '__qualname__', '?')}>", "exec")
        local_ns: dict = {}
        exec(code, global_ns, local_ns)

        func_name = getattr(original_func, "__name__", "")
        result = local_ns.get(func_name) or global_ns.get(func_name)
        if result is not None:
            return result

        # fallback: first callable defined in local_ns
        for v in local_ns.values():
            if callable(v) and not isinstance(v, type):
                return v

        return None
    except Exception:
        return None


def apply(snap: Snapshot, new_func: Any) -> bool:
    """Apply new_func to replace original in sys.modules. Returns success."""
    module = sys.modules.get(snap.module_name)
    if module is None:
        return False

    try:
        orig_freevars = getattr(getattr(snap.original_func, "__code__", None), "co_freevars", ())
        new_freevars = getattr(getattr(new_func, "__code__", None), "co_freevars", ())

        if orig_freevars != new_freevars:
            return _apply_proxy(module, snap.func_name, snap.original_func, new_func)

        setattr(module, snap.func_name, new_func)
        return True
    except Exception:
        return False


def rollback(snap: Snapshot) -> bool:
    module = sys.modules.get(snap.module_name)
    if module is None:
        return False
    try:
        setattr(module, snap.func_name, snap.original_func)
        return True
    except Exception:
        return False


def _apply_proxy(module: Any, func_name: str, original_func: Any, new_func: Any) -> bool:
    """Wrapper proxy for closure-mismatched functions."""
    @functools.wraps(original_func)
    def proxy(*args, **kwargs):
        return new_func(*args, **kwargs)

    try:
        setattr(module, func_name, proxy)
        return True
    except Exception:
        return False
