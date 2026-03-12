from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types


class FastAPIIntegration:
    """
    Patches APIRouter.add_api_route so every route handler is wrapped with
    cauterize's heal decorator at registration time.
    """

    watch_modules = ["fastapi", "fastapi.routing"]

    def on_module_imported(self, module_name: str, module: "types.ModuleType", mode: str) -> None:
        if module_name == "fastapi.routing":
            _patch_router(module, mode)

    def wrap_endpoint(self, func: Any, mode: str) -> Any:
        from .._heal import heal
        return heal(func)

    def wrap_startup_handler(self, func: Any) -> Any:
        from .._startup import StartupWrapper
        return StartupWrapper(func)


def _patch_router(routing_module: Any, mode: str) -> None:
    """Monkeypatch APIRouter.add_api_route to wrap handlers on registration."""
    try:
        APIRouter = routing_module.APIRouter
    except AttributeError:
        return

    if getattr(APIRouter, "_cauterize_patched", False):
        return

    original_add_api_route = APIRouter.add_api_route

    def _patched_add_api_route(self_router, path, endpoint, **kwargs):  # type: ignore[no-untyped-def]
        from .. import _config
        current_mode = _config.get().mode
        endpoint = _maybe_wrap(endpoint, current_mode)
        return original_add_api_route(self_router, path, endpoint, **kwargs)

    APIRouter.add_api_route = _patched_add_api_route
    APIRouter._cauterize_patched = True  # type: ignore[attr-defined]


def _maybe_wrap(func: Any, mode: str) -> Any:
    """
    Wrap func according to mode:
    - "auto": wrap unless @cauterize.exclude is set
    - "manual": wrap only if @cauterize.heal is set (marker placed by the decorator)
    """
    if getattr(func, "_cauterize_exclude", False):
        return func
    if mode == "auto" or getattr(func, "_cauterize_heal", False):
        from .._heal import heal
        if not getattr(func, "_cauterize_wrapped", False):
            return heal(func)
    return func
