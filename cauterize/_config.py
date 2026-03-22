from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._notify import SlackNotifier
    from ._jira import JiraCard


@dataclass
class Config:
    confidence_threshold: float = 0.85
    max_retries: int = 3
    model: str = "anthropic:claude-opus-4-6"
    dry_run: bool = False
    mode: str = "auto"          # "auto" | "manual"
    audit_path: str | None = None
    slack: Any = None           # SlackNotifier
    jira: Any = None            # JiraCard
    github: Any = None          # GitHubPR


_config = Config()


def configure(**kwargs: Any) -> None:
    global _config
    unknown = set(kwargs) - set(_config.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown config keys: {unknown}")
    if "mode" in kwargs:
        set_mode(kwargs.pop("mode"))
    for k, v in kwargs.items():
        setattr(_config, k, v)


def get() -> Config:
    return _config


def set_mode(mode: str) -> None:
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode must be 'auto' or 'manual', got {mode!r}")
    _config.mode = mode
