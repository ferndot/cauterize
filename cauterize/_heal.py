from __future__ import annotations

import asyncio
import functools
import logging
import threading
from typing import Any, Callable

from . import _config, _context, _safety, _prompt, _ai_client, _validator, _patcher, _audit, _escalation
from ._context import HealContext

log = logging.getLogger("cauterize")

_notification_results: dict[str, dict] = {}  # func_qualname -> notification results


def get_notification_results(func_qualname: str) -> dict | None:
    return _notification_results.get(func_qualname)


def heal(func: Callable) -> Callable:
    """
    Decorator: intercept exceptions and attempt AI-powered healing.
    Supports both sync and async functions.
    Idempotent — wrapping an already-wrapped function is a no-op.
    """
    if getattr(func, "__cauterize_healed__", False):
        return func

    _healed = [None]

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if _healed[0] is not None:
                log.info("cauterize: [cache] serving healed %s — patch already applied, no LLM call", func.__qualname__)
                return await _healed[0](*args, **kwargs)
            return await _run_async(func, args, kwargs, _healed)

        def _async_reset():
            _healed[0] = None

        async_wrapper.__cauterize_healed__ = True
        async_wrapper.__cauterize_reset__ = _async_reset
        if getattr(func, '__cauterize_protected__', False):
            async_wrapper.__cauterize_protected__ = True  # type: ignore[attr-defined]
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if _healed[0] is not None:
                log.info("cauterize: [cache] serving healed %s — patch already applied, no LLM call", func.__qualname__)
                return _healed[0](*args, **kwargs)
            result = _run_sync(func, args, kwargs, _healed)
            if _healed[0] is not None:
                sync_wrapper.__cauterize_healed_fn__ = _healed[0]
            return result

        def _sync_reset():
            _healed[0] = None
            if hasattr(sync_wrapper, '__cauterize_healed_fn__'):
                del sync_wrapper.__cauterize_healed_fn__

        sync_wrapper.__cauterize_healed__ = True
        sync_wrapper.__cauterize_reset__ = _sync_reset
        if getattr(func, '__cauterize_protected__', False):
            sync_wrapper.__cauterize_protected__ = True  # type: ignore[attr-defined]
        return sync_wrapper


# ── sync heal loop ─────────────────────────────────────────────────────────────

def _run_sync(func: Any, args: tuple, kwargs: dict, _healed: list) -> Any:
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

                log.info(
                    "cauterize: caught %s in %s — attempt %d/%d",
                    type(exc).__name__, current_func.__qualname__, attempt, cfg.max_retries,
                )

                result, current_func = _attempt_heal_sync(
                    current_func, exc, args, kwargs, attempt, cfg, _healed
                )
                if result is not _HEAL_FAILED:
                    return result

                if attempt == cfg.max_retries:
                    ctx = _context.extract(exc, func)
                    log.info("cauterize: hot-path exhausted — escalating %s to agentic path", func.__qualname__)
                    _escalation.escalate(func, ctx)
                raise exc


_HEAL_FAILED = object()


def _attempt_heal_sync(current_func, exc, args, kwargs, attempt, cfg, _healed):
    ctx = _context.extract(exc, current_func)
    prompt = _prompt.build(ctx, current_func)
    ai_resp = _ai_client.request_fix(current_func, exc, prompt)

    if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
        log.warning(
            "cauterize: rejected patch for %s — confidence %.0f%% below threshold %.0f%%",
            current_func.__qualname__,
            (ai_resp.confidence if ai_resp else 0.0) * 100,
            cfg.confidence_threshold * 100,
        )
        _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
        return _HEAL_FAILED, current_func

    log.info(
        "cauterize: AI generated patch for %s (%.0f%% confidence)\n--- patch ---\n%s\n--- end patch ---",
        current_func.__qualname__, ai_resp.confidence * 100, ai_resp.fixed_source,
    )

    original_source = _context.get_source(current_func)
    err = _validator.validate(original_source, ai_resp.fixed_source, current_func)
    if err:
        log.warning("cauterize: validation failed for %s — %s", current_func.__qualname__, err)
        _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
        return _HEAL_FAILED, current_func

    new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
    if new_func is None:
        log.warning("cauterize: compile failed for %s", current_func.__qualname__)
        _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
        return _HEAL_FAILED, current_func

    # pre-commit replay: verify fix with original args before patching
    try:
        result = new_func(*args, **kwargs)
    except Exception as replay_exc:
        log.warning("cauterize: replay failed for %s — %s", current_func.__qualname__, replay_exc)
        _audit.write(ctx, "failed", ai_resp.confidence, attempt,
                     f"replay failed: {replay_exc}")
        return _HEAL_FAILED, current_func

    # replay succeeded — commit the patch
    snap = _patcher.snapshot(current_func)
    if not cfg.dry_run:
        _patcher.apply(snap, new_func)

    log.info(
        "cauterize: patch applied to %s — %s",
        current_func.__qualname__, ai_resp.explanation,
    )
    _audit.write(ctx, "healed", ai_resp.confidence, attempt, ai_resp.explanation)
    _dispatch_notifications(ctx, ai_resp)
    new_func.__cauterize_original_source__ = original_source
    new_func.__cauterize_patched_source__ = ai_resp.fixed_source
    _healed[0] = new_func

    return result, new_func


# ── async heal loop ────────────────────────────────────────────────────────────

async def _run_async(func: Any, args: tuple, kwargs: dict, _healed: list) -> Any:
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

            log.info(
                "cauterize: caught %s in %s — attempt %d/%d",
                type(exc).__name__, current_func.__qualname__, attempt, cfg.max_retries,
            )

            ctx = _context.extract(exc, current_func)
            prompt = _prompt.build(ctx, current_func)

            # run blocking AI call off the event loop
            ai_resp = await loop.run_in_executor(
                None, _ai_client.request_fix, current_func, exc, prompt
            )

            if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
                log.warning(
                    "cauterize: rejected patch for %s — confidence %.0f%% below threshold %.0f%%",
                    current_func.__qualname__,
                    (ai_resp.confidence if ai_resp else 0.0) * 100,
                    cfg.confidence_threshold * 100,
                )
                _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
                raise

            log.info(
                "cauterize: AI generated patch for %s (%.0f%% confidence)\n--- patch ---\n%s\n--- end patch ---",
                current_func.__qualname__, ai_resp.confidence * 100, ai_resp.fixed_source,
            )

            original_source = _context.get_source(current_func)
            err = _validator.validate(original_source, ai_resp.fixed_source, current_func)
            if err:
                log.warning("cauterize: validation failed for %s — %s", current_func.__qualname__, err)
                _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
                raise

            new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
            if new_func is None:
                log.warning("cauterize: compile failed for %s", current_func.__qualname__)
                _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
                raise

            # pre-commit replay (async)
            try:
                result = await new_func(*args, **kwargs)
            except Exception as replay_exc:
                log.warning("cauterize: replay failed for %s — %s", current_func.__qualname__, replay_exc)
                _audit.write(ctx, "failed", ai_resp.confidence, attempt,
                             f"replay failed: {replay_exc}")
                raise exc

            snap = _patcher.snapshot(current_func)
            if not cfg.dry_run:
                _patcher.apply(snap, new_func)

            log.info(
                "cauterize: patch applied to %s — %s",
                current_func.__qualname__, ai_resp.explanation,
            )
            _audit.write(ctx, "healed", ai_resp.confidence, attempt, ai_resp.explanation)
            _dispatch_notifications(ctx, ai_resp)
            new_func.__cauterize_original_source__ = original_source
            new_func.__cauterize_patched_source__ = ai_resp.fixed_source
            _healed[0] = new_func
            current_func = new_func

            return result

    ctx = _context.extract(exc, func)
    log.info("cauterize: hot-path exhausted — escalating %s to agentic path", func.__qualname__)
    _escalation.escalate(func, ctx)
    raise RuntimeError(f"cauterize: exhausted retries for {func.__qualname__!r}")


# ── post-heal dispatch ─────────────────────────────────────────────────────────

def _dispatch_notifications(ctx: _context.ExceptionContext, ai_resp: Any) -> None:
    import inspect
    func_qualname = getattr(ctx.target_func, "__qualname__", "<unknown>")
    try:
        source_file = inspect.getfile(ctx.target_func)
    except (OSError, TypeError):
        source_file = ""
    heal_ctx = HealContext(
        func_qualname=func_qualname,
        exc_type=ctx.exc_type,
        exc_message=ctx.exc_message,
        fixed_source=ai_resp.fixed_source,
        explanation=ai_resp.explanation,
        confidence=ai_resp.confidence,
        source_file=source_file,
        original_source=_context.get_source(ctx.target_func),
    )
    threading.Thread(
        target=_post_heal_dispatch,
        args=(heal_ctx,),
        daemon=True,
    ).start()


def _post_heal_dispatch(heal_ctx: HealContext) -> None:
    from . import _config as cfg_mod
    cfg = cfg_mod.get()
    results: dict = {}

    card_url = None
    if cfg.jira:
        try:
            card_url = cfg.jira.create(heal_ctx)
            results["jira_url"] = card_url
        except Exception:
            pass

    pr_url = None
    if cfg.github:
        try:
            pr_url = cfg.github.create(heal_ctx, jira_url=card_url)
            if pr_url:
                results["github_pr_url"] = pr_url
        except Exception:
            pass

    if cfg.slack:
        try:
            cfg.slack.send(heal_ctx, card_url, github_pr_url=pr_url)
            results["slack_sent"] = True
        except Exception:
            pass

    _notification_results[heal_ctx.func_qualname] = results
