from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types


class DjangoIntegration:
    """
    Patches django.views.View.dispatch (CBV) and wraps FBV handlers by hooking
    into django.urls.path/re_path at registration time.
    """

    watch_modules = ["django.views", "django.views.generic.base", "django.urls"]

    def on_module_imported(self, module_name: str, module: "types.ModuleType", mode: str) -> None:
        if module_name == "django.views.generic.base":
            _patch_view_dispatch(module, mode)
        elif module_name == "django.urls":
            _patch_url_registration(module, mode)

    def wrap_endpoint(self, func: Any, mode: str) -> Any:
        from .._heal import heal
        return heal(func)

    def wrap_startup_handler(self, func: Any) -> Any:
        from .._startup import StartupWrapper
        return StartupWrapper(func)


def _patch_view_dispatch(views_module: Any, mode: str) -> None:
    """Wrap View.dispatch so class-based views are healed per-request."""
    try:
        View = views_module.View
    except AttributeError:
        return

    if getattr(View, "_cauterize_patched", False):
        return

    original_dispatch = View.dispatch

    def _patched_dispatch(self_view, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        from .. import _config
        current_mode = _config.get().mode
        handler = getattr(self_view, request.method.lower(), self_view.http_method_not_allowed)
        wrapped = _maybe_wrap(handler, current_mode)
        if wrapped is not handler:
            return wrapped(request, *args, **kwargs)
        return original_dispatch(self_view, request, *args, **kwargs)

    View.dispatch = _patched_dispatch
    View._cauterize_patched = True  # type: ignore[attr-defined]


def _patch_url_registration(urls_module: Any, mode: str) -> None:
    """Wrap FBV handlers registered via path() or re_path()."""
    for fn_name in ("path", "re_path"):
        original = getattr(urls_module, fn_name, None)
        if original is None or getattr(original, "_cauterize_patched", False):
            continue

        def _make_patched(orig):  # noqa: ANN001
            def _patched(route, view, *args, **kwargs):  # type: ignore[no-untyped-def]
                from .. import _config
                current_mode = _config.get().mode
                view = _maybe_wrap(view, current_mode)
                return orig(route, view, *args, **kwargs)
            _patched._cauterize_patched = True  # type: ignore[attr-defined]
            return _patched

        setattr(urls_module, fn_name, _make_patched(original))


def _maybe_wrap(func: Any, mode: str) -> Any:
    if getattr(func, "_cauterize_exclude", False):
        return func
    if mode == "auto" or getattr(func, "_cauterize_heal", False):
        from .._heal import heal
        if not getattr(func, "_cauterize_wrapped", False):
            return heal(func)
    return func
