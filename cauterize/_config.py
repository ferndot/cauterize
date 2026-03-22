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


_config = Config()


def configure(**kwargs: Any) -> None:
    global _config
    unknown = set(kwargs) - {f.name for f in _config.__dataclass_fields__.values()}
    if unknown:
        raise ValueError(f"Unknown config keys: {unknown}")
    for k, v in kwargs.items():
        setattr(_config, k, v)


def get() -> Config:
    return _config


def set_mode(mode: str) -> None:
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode must be 'auto' or 'manual', got {mode!r}")
    _config.mode = mode
