from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types


class CeleryIntegration:
    """
    Patches celery.app.task.Task so every task's run() method is wrapped with
    cauterize's heal decorator. Unlike HTTP requests, Celery tasks cannot replay
    pre-commit verification because the message is already acked; cauterize
    patches the function and relies on Celery's own retry mechanism to re-run.
    """

    watch_modules = ["celery", "celery.app.task"]

    def on_module_imported(self, module_name: str, module: "types.ModuleType", mode: str) -> None:
        if module_name == "celery.app.task":
            _patch_task_class(module, mode)

    def wrap_endpoint(self, func: Any, mode: str) -> Any:
        return _wrap_task_func(func, mode)

    def wrap_startup_handler(self, func: Any) -> Any:
        return func  # Celery has no startup handlers in the HTTP sense


def _patch_task_class(task_module: Any, mode: str) -> None:
    """Patch Task.__init_subclass__ so subclasses get their run() wrapped."""
    try:
        Task = task_module.Task
    except AttributeError:
        return

    if getattr(Task, "_cauterize_patched", False):
        return

    original_init_subclass = Task.__dict__.get("__init_subclass__")

    @classmethod  # type: ignore[misc]
    def _patched_init_subclass(cls, **kwargs):  # type: ignore[no-untyped-def]
        if original_init_subclass:
            original_init_subclass.__func__(cls, **kwargs)

        from .. import _config
        current_mode = _config.get().mode
        _wrap_task_run(cls, current_mode)

    Task.__init_subclass__ = _patched_init_subclass
    Task._cauterize_patched = True  # type: ignore[attr-defined]


def _wrap_task_run(task_cls: Any, mode: str) -> None:
    """Wrap the run() method of a Task subclass if eligible."""
    run = task_cls.__dict__.get("run")
    if run is None:
        return
    if getattr(run, "_cauterize_exclude", False):
        return
    if mode == "auto" or getattr(run, "_cauterize_heal", False):
        task_cls.run = _wrap_task_func(run, mode)


def _wrap_task_func(func: Any, mode: str) -> Any:
    """
    Wrap a Celery task function. No pre-commit replay — rely on Celery retry.
    bind=True tasks receive 'self' (the task instance) as the first positional
    arg; the wrapper is transparent to that.
    """
    if getattr(func, "_cauterize_wrapped", False):
        return func
    if getattr(func, "_cauterize_exclude", False):
        return func

    from .._heal import heal

    wrapped = heal(func)

    @functools.wraps(func)
    def _celery_proxy(*args, **kwargs):  # type: ignore[no-untyped-def]
        return wrapped(*args, **kwargs)

    _celery_proxy._cauterize_wrapped = True  # type: ignore[attr-defined]
    return _celery_proxy
