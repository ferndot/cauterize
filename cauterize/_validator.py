from __future__ import annotations

import ast
import textwrap
from typing import Any


_DANGEROUS_NODES = frozenset({
    "eval", "exec", "compile", "__import__",
})

# attribute access patterns we reject: os.system, subprocess.*, socket.*, open
_DANGEROUS_ATTRS = frozenset({
    "system", "popen", "execv", "execve",   # os
    "call", "run", "Popen", "check_output", # subprocess
    "socket",                                # socket
})

_DANGEROUS_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__", "open",
})


def validate(original_source: str, fixed_source: str, original_func: Any) -> str | None:
    """
    Returns an error string if invalid, None if the patch passes all checks.
    """
    # 1. must compile
    try:
        fixed_tree = ast.parse(textwrap.dedent(fixed_source))
    except SyntaxError as e:
        return f"SyntaxError in patch: {e}"

    # 2. signature must not change
    try:
        orig_tree = ast.parse(textwrap.dedent(original_source))
        sig_err = _check_signatures(orig_tree, fixed_tree)
        if sig_err:
            return sig_err
    except SyntaxError:
        pass    # original may not parse cleanly in isolation — skip check

    # 3. no new imports
    try:
        orig_tree = ast.parse(textwrap.dedent(original_source))
        if _has_new_imports(orig_tree, fixed_tree):
            return "Patch adds new imports"
    except SyntaxError:
        pass

    # 4. dangerous pattern check
    danger = _find_dangerous_patterns(fixed_tree)
    if danger:
        return f"Dangerous patterns detected: {', '.join(danger)}"

    # 5. line count sanity — no more than 3× the original
    orig_lines = _count_code_lines(original_source)
    fixed_lines = _count_code_lines(fixed_source)
    if orig_lines > 0 and fixed_lines > orig_lines * 3:
        return f"Patch too large: {fixed_lines} lines vs original {orig_lines}"

    return None


def _check_signatures(orig_tree: ast.AST, fixed_tree: ast.AST) -> str | None:
    orig_funcs = {n.name: n for n in ast.walk(orig_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    fixed_funcs = {n.name: n for n in ast.walk(fixed_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for name, orig_node in orig_funcs.items():
        if name not in fixed_funcs:
            continue
        if ast.dump(orig_node.args) != ast.dump(fixed_funcs[name].args):
            return f"Signature of '{name}' changed"
    return None


def _has_new_imports(orig_tree: ast.AST, fixed_tree: ast.AST) -> bool:
    def collect(tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        return names

    return bool(collect(fixed_tree) - collect(orig_tree))


def _find_dangerous_patterns(tree: ast.AST) -> list[str]:
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name:
                base = name.split(".")[-1]
                if name in _DANGEROUS_BUILTINS or base in _DANGEROUS_ATTRS:
                    found.append(name)

    return found


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        n = node.func
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
            return ".".join(reversed(parts))
    return None


def _count_code_lines(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip())
