from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable
from types import ModuleType


@runtime_checkable
class Integration(Protocol):
    """
    Implement this protocol to add cauterize support for any framework.

    Third-party packages register via pyproject.toml entry points:

        [project.entry-points."cauterize.integrations"]
        myframework = "cauterize_myframework:MyFrameworkIntegration"
    """

    watch_modules: list[str]
    """Module names that trigger on_module_imported when imported."""

    def on_module_imported(
        self,
        module_name: str,
        module: ModuleType,
        mode: str,
    ) -> None:
        """
        Patch the module after import. Called once per watched module.
        mode is "auto" (opt-out) or "manual" (opt-in).
        """
        ...

    def wrap_endpoint(self, func: Callable, mode: str) -> Callable:
        """Wrap a single endpoint function according to mode."""
        ...

    def wrap_startup_handler(self, func: Callable) -> Callable:
        """
        Wrap a startup/shutdown lifecycle handler.
        Default implementation is a no-op — override when the framework
        has startup hooks worth protecting.
        """
        return func
