"""
cauterize — self-healing Python module.

Quick start (auto mode — heals all framework routes):

    import cauterize
    cauterize.install()

Quick start (manual mode — opt-in per function):

    import cauterize
    cauterize.install(mode="manual")

    @cauterize.heal
    def my_function():
        ...

Configuration:

    cauterize.configure(
        api_key="sk-ant-...",           # defaults to ANTHROPIC_API_KEY env var
        confidence_threshold=0.85,
        max_retries=3,
        slack=cauterize.SlackNotifier(webhook_url="https://hooks.slack.com/..."),
        jira=cauterize.JiraCard(
            base_url="https://myorg.atlassian.net",
            project_key="ENG",
            auth=("user@example.com", "api_token"),
        ),
        audit_path="/var/log/cauterize.jsonl",
        dry_run=False,
    )
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from ._config import configure
from ._heal import heal as _heal_decorator, get_notification_results
from ._hook import install_hook
from ._registry import get_registry
from .integrations.slack import SlackNotifier
from .integrations.jira import JiraCard
from .integrations.github import GitHubPR


__version__ = "0.1.0"
__all__ = [
    "install",
    "configure",
    "heal",
    "exclude",
    "protect",
    "SlackNotifier",
    "JiraCard",
    "GitHubPR",
    "get_notification_results",
    "__version__",
]


def install(mode: str = "auto") -> None:
    """
    Instrument the application.

    mode="auto"   — wrap every framework route/task (opt-out with @cauterize.exclude)
    mode="manual" — wrap nothing automatically (opt-in with @cauterize.heal)

    Registers the import hook so that frameworks imported *after* this call are
    also patched. Frameworks already imported are patched immediately via
    sys.modules inspection inside each integration.
    """
    from . import _config
    _config.set_mode(mode)

    registry = get_registry()
    registry.auto_discover()
    registry.load_entry_points()

    # Patch any framework modules that are already in sys.modules
    import sys
    for module_name in list(registry._target_map):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for integration in registry.integrations_for(module_name):
            try:
                integration.on_module_imported(module_name, module, mode)
            except Exception:
                pass

    install_hook()


def heal(func: Callable) -> Callable:
    """
    Decorator: mark a function for cauterize healing in manual mode, or add
    healing to an individual function regardless of mode.

    Usage::

        @cauterize.heal
        def process_order(order_id: int) -> dict:
            ...
    """
    wrapped = _heal_decorator(func)
    wrapped._cauterize_heal = True  # type: ignore[attr-defined]
    return wrapped


def exclude(func: Callable) -> Callable:
    """
    Decorator: prevent cauterize from wrapping this function in auto mode.

    Usage::

        @cauterize.exclude
        def untouchable():
            ...
    """
    func._cauterize_exclude = True  # type: ignore[attr-defined]
    return func


def protect(func: Callable) -> Callable:
    """
    Decorator: mark a function as containing high-risk logic (financial ops,
    bulk mutations, etc.) that should never be auto-patched.

    Cauterize will detect exceptions but refuse to heal and re-raise immediately.

    Usage::

        @cauterize.protect
        def process_payment(order_id: int) -> dict:
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    wrapper.__cauterize_protected__ = True  # type: ignore[attr-defined]
    return wrapper
