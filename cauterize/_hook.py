from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys

from ._registry import get_registry


class _CauterizeImportHook(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """
    Generic import hook that intercepts framework module loads and applies
    registered Integration patches. Knows nothing about specific frameworks.
    """

    def find_spec(self, fullname: str, path, target=None):
        if not get_registry().has_target(fullname):
            return None

        # temporarily remove ourselves to allow the real import to proceed
        sys.meta_path.remove(self)
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            sys.meta_path.insert(0, self)

        if spec is None:
            return None

        spec.loader = self
        return spec

    def create_module(self, spec):
        return None     # use default module creation semantics

    def exec_module(self, module) -> None:
        # run the real loader first
        sys.meta_path.remove(self)
        try:
            spec = importlib.util.find_spec(module.__name__)
            if spec and spec.loader and spec.loader is not self:
                spec.loader.exec_module(module)
        finally:
            sys.meta_path.insert(0, self)

        from . import _config
        mode = _config.get().mode

        # dispatch to all integrations watching this module
        for integration in get_registry().integrations_for(module.__name__):
            try:
                integration.on_module_imported(module.__name__, module, mode)
            except Exception:
                pass    # never let patching crash the application


_hook = _CauterizeImportHook()


def install_hook() -> None:
    if _hook not in sys.meta_path:
        sys.meta_path.insert(0, _hook)
