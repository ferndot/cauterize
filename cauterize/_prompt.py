from __future__ import annotations

from ._context import ExceptionContext, get_source


def build(ctx: ExceptionContext, func) -> str:
    source = get_source(func)
    return f"""\
You are fixing a Python runtime error. Return ONLY a JSON object matching the schema below.

## Function to fix

```python
{source}
```

## Exception

{ctx.exc_type}: {ctx.exc_message}

## Traceback

{_format_traceback(ctx)}

## Local variable types at error site

{_format_locals(ctx)}

## Constraints

- Do NOT change the function signature
- Do NOT add new imports
- Do NOT add file, network, or subprocess operations
- Do NOT use eval, exec, or __import__
- Keep the fix minimal — change as few lines as possible
- Return the complete, syntactically valid function definition

## Required JSON schema

{{
  "fixed_source": "<complete fixed function>",
  "confidence": <float 0.0–1.0>,
  "explanation": "<one sentence>",
  "is_safe_to_auto_apply": <bool>,
  "safety_concerns": "<empty string or description>"
}}"""


def _format_traceback(ctx: ExceptionContext) -> str:
    lines = []
    for frame in ctx.frames:
        lines.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.func_name}')
    lines.append(f"{ctx.exc_type}: {ctx.exc_message}")
    return "\n".join(lines)


def _format_locals(ctx: ExceptionContext) -> str:
    if not ctx.target_frame or not ctx.target_frame.locals:
        return "(none)"
    lines = []
    for name, type_repr in ctx.target_frame.locals.items():
        type_name = type_repr.split(":")[0]
        lines.append(f"  {name}: {type_name}")
    return "\n".join(lines)
