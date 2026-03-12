from __future__ import annotations

import asyncio
import functools
import threading
from typing import Any, Callable

from . import _config, _context, _safety, _prompt, _ai_client, _validator, _patcher, _audit
from ._context import HealContext


def heal(func: Callable) -> Callable:
    """
    Decorator: intercept exceptions and attempt AI-powered healing.
    Supports both sync and async functions.
    Idempotent — wrapping an already-wrapped function is a no-op.
    """
    if getattr(func, "__cauterize_healed__", False):
        return func

    func.__cauterize_heal__ = True

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await _run_async(func, args, kwargs)
        async_wrapper.__cauterize_healed__ = True
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            return _run_sync(func, args, kwargs)
        sync_wrapper.__cauterize_healed__ = True
        return sync_wrapper


# ── sync heal loop ─────────────────────────────────────────────────────────────

def _run_sync(func: Any, args: tuple, kwargs: dict) -> Any:
    cfg = _config.get()
    current_func = func

    with _safety.get_lock(func):
        for attempt in range(1, cfg.max_retries + 1):
            try:
                return current_func(*args, **kwargs)
            except Exception as exc:
                if not _safety.is_eligible(current_func, exc):
                    raise
                if not _safety.can_attempt(current_func, exc):
                    raise

                _safety.record_attempt(current_func, exc)

                result, current_func = _attempt_heal_sync(
                    current_func, exc, args, kwargs, attempt, cfg
                )
                if result is not _HEAL_FAILED:
                    return result
                raise exc


_HEAL_FAILED = object()


def _attempt_heal_sync(current_func, exc, args, kwargs, attempt, cfg):
    ctx = _context.extract(exc, current_func)
    prompt = _prompt.build(ctx, current_func)
    ai_resp = _ai_client.request_fix(current_func, exc, prompt)

    if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
        _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
        return _HEAL_FAILED, current_func

    original_source = _context.get_source(current_func)
    err = _validator.validate(original_source, ai_resp.fixed_source, current_func)
    if err:
        _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
        return _HEAL_FAILED, current_func

    new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
    if new_func is None:
        _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
        return _HEAL_FAILED, current_func

    # pre-commit replay: verify fix with original args before patching
    try:
        result = new_func(*args, **kwargs)
    except Exception as replay_exc:
        _audit.write(ctx, "failed", ai_resp.confidence, attempt,
                     f"replay failed: {replay_exc}")
        return _HEAL_FAILED, current_func

    # replay succeeded — commit the patch
    snap = _patcher.snapshot(current_func)
    if not cfg.dry_run:
        _patcher.apply(snap, new_func)

    _audit.write(ctx, "healed", ai_resp.confidence, attempt, ai_resp.explanation)
    _dispatch_notifications(ctx, ai_resp)

    return result, new_func


# ── async heal loop ────────────────────────────────────────────────────────────

async def _run_async(func: Any, args: tuple, kwargs: dict) -> Any:
    cfg = _config.get()
    current_func = func
    loop = asyncio.get_event_loop()

    for attempt in range(1, cfg.max_retries + 1):
        try:
            return await current_func(*args, **kwargs)
        except Exception as exc:
            if not _safety.is_eligible(current_func, exc):
                raise
            if not _safety.can_attempt(current_func, exc):
                raise

            _safety.record_attempt(current_func, exc)

            ctx = _context.extract(exc, current_func)
            prompt = _prompt.build(ctx, current_func)

            # run blocking AI call off the event loop
            ai_resp = await loop.run_in_executor(
                None, _ai_client.request_fix, current_func, exc, prompt
            )

            if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
                _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
                raise

            original_source = _context.get_source(current_func)
            err = _validator.validate(original_source, ai_resp.fixed_source, current_func)
            if err:
                _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
                raise

            new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
            if new_func is None:
                _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
                raise

            # pre-commit replay (async)
            try:
                result = await new_func(*args, **kwargs)
            except Exception as replay_exc:
                _audit.write(ctx, "failed", ai_resp.confidence, attempt,
                             f"replay failed: {replay_exc}")
                raise exc

            snap = _patcher.snapshot(current_func)
            if not cfg.dry_run:
                _patcher.apply(snap, new_func)

            _audit.write(ctx, "healed", ai_resp.confidence, attempt, ai_resp.explanation)
            _dispatch_notifications(ctx, ai_resp)
            current_func = new_func

            return result

    raise RuntimeError(f"cauterize: exhausted retries for {func.__qualname__!r}")


# ── post-heal dispatch ─────────────────────────────────────────────────────────

def _dispatch_notifications(ctx: _context.ExceptionContext, ai_resp: Any) -> None:
    func_qualname = getattr(ctx.target_func, "__qualname__", "<unknown>")
    heal_ctx = HealContext(
        func_qualname=func_qualname,
        exc_type=ctx.exc_type,
        exc_message=ctx.exc_message,
        fixed_source=ai_resp.fixed_source,
        explanation=ai_resp.explanation,
        confidence=ai_resp.confidence,
    )
    threading.Thread(
        target=_post_heal_dispatch,
        args=(heal_ctx,),
        daemon=True,
    ).start()


def _post_heal_dispatch(heal_ctx: HealContext) -> None:
    # lazy imports to avoid circular dependency at module load time
    from . import _config as cfg_mod
    cfg = cfg_mod.get()

    card_url = None
    if cfg.jira:
        card_url = cfg.jira.create(heal_ctx)

    if cfg.slack:
        try:
            cfg.slack.send(heal_ctx, card_url)
        except Exception:
            pass
