from __future__ import annotations

import asyncio
import functools
from typing import Any


class StartupWrapper:
    """
    Wraps a startup/shutdown handler with heal-on-failure semantics.

    Unlike request handlers, startup handlers must fully complete before the
    app accepts traffic. After a successful heal we re-invoke the patched
    function rather than returning a value from replay.

    Pre-commit replay is skipped here: the startup side-effects (DB connections,
    cache warm-up, etc.) are idempotent by convention, so re-running is safe.
    """

    def __init__(self, func: Any) -> None:
        self._func = func
        self._is_async = asyncio.iscoroutinefunction(func)
        functools.update_wrapper(self, func)

    def __call__(self):
        if self._is_async:
            return self._run_async()
        return self._run_sync()

    def _run_sync(self) -> None:
        from . import _config, _context, _safety, _prompt, _ai_client, _validator, _patcher, _audit

        cfg = _config.get()
        current_func = self._func

        for attempt in range(1, cfg.max_retries + 1):
            try:
                current_func()
                return
            except Exception as exc:
                if not _safety.is_eligible(current_func, exc):
                    raise
                if not _safety.can_attempt(current_func, exc):
                    raise

                _safety.record_attempt(current_func, exc)
                ctx = _context.extract(exc, current_func)
                prompt = _prompt.build(ctx, current_func)
                ai_resp = _ai_client.request_fix(current_func, exc, prompt)

                if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
                    _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
                    raise

                err = _validator.validate(
                    _context.get_source(current_func), ai_resp.fixed_source, current_func
                )
                if err:
                    _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
                    raise

                new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
                if new_func is None:
                    _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
                    raise

                snap = _patcher.snapshot(current_func)
                if not cfg.dry_run:
                    _patcher.apply(snap, new_func)

                current_func = new_func
                # loop: re-invoke the fixed function

    async def _run_async(self) -> None:
        from . import _config, _context, _safety, _prompt, _ai_client, _validator, _patcher, _audit

        cfg = _config.get()
        current_func = self._func

        for attempt in range(1, cfg.max_retries + 1):
            try:
                await current_func()
                return
            except Exception as exc:
                if not _safety.is_eligible(current_func, exc):
                    raise
                if not _safety.can_attempt(current_func, exc):
                    raise

                _safety.record_attempt(current_func, exc)
                ctx = _context.extract(exc, current_func)
                prompt = _prompt.build(ctx, current_func)

                loop = asyncio.get_event_loop()
                ai_resp = await loop.run_in_executor(
                    None, _ai_client.request_fix, current_func, exc, prompt
                )

                if not ai_resp or ai_resp.confidence < cfg.confidence_threshold:
                    _audit.write(ctx, "rejected", ai_resp.confidence if ai_resp else 0.0, attempt)
                    raise

                err = _validator.validate(
                    _context.get_source(current_func), ai_resp.fixed_source, current_func
                )
                if err:
                    _audit.write(ctx, "failed", ai_resp.confidence, attempt, err)
                    raise

                new_func = _patcher.compile_function(ai_resp.fixed_source, current_func)
                if new_func is None:
                    _audit.write(ctx, "failed", ai_resp.confidence, attempt, "compile failed")
                    raise

                snap = _patcher.snapshot(current_func)
                if not cfg.dry_run:
                    _patcher.apply(snap, new_func)

                current_func = new_func
