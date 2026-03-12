from __future__ import annotations

import importlib.metadata
import importlib.util
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .integrations._base import Integration


class IntegrationRegistry:
    def __init__(self) -> None:
        self._integrations: list[Integration] = []
        self._target_map: dict[str, list[Integration]] = defaultdict(list)

    def register(self, integration: Integration) -> None:
        self._integrations.append(integration)
        for module_name in integration.watch_modules:
            self._target_map[module_name].append(integration)

    def auto_discover(self) -> None:
        """Register built-in integrations for any installed frameworks."""
        if importlib.util.find_spec("fastapi"):
            from .integrations.fastapi import FastAPIIntegration
            self.register(FastAPIIntegration())
        if importlib.util.find_spec("django"):
            from .integrations.django import DjangoIntegration
            self.register(DjangoIntegration())
        if importlib.util.find_spec("celery"):
            from .integrations.celery import CeleryIntegration
            self.register(CeleryIntegration())

    def load_entry_points(self) -> None:
        """Auto-discover third-party integrations via entry points."""
        try:
            for ep in importlib.metadata.entry_points(group="cauterize.integrations"):
                try:
                    cls = ep.load()
                    self.register(cls())
                except Exception:
                    pass
        except Exception:
            pass

    def has_target(self, module_name: str) -> bool:
        return module_name in self._target_map

    def integrations_for(self, module_name: str) -> list[Integration]:
        return self._target_map.get(module_name, [])


_registry = IntegrationRegistry()


def get_registry() -> IntegrationRegistry:
    return _registry
