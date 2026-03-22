from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._notify import SlackNotifier
    from ._jira import JiraCard
    from ._provider import Provider


@dataclass
class Config:
    confidence_threshold: float = 0.85
    max_retries: int = 3
    model: str = "claude-opus-4-6"
    dry_run: bool = False
    mode: str = "auto"          # "auto" | "manual"
    audit_path: str | None = None
    slack: Any = None           # SlackNotifier
    jira: Any = None            # JiraCard
    provider: Any = None        # Provider | None — auto-detected when None


_config = Config()


def configure(**kwargs: Any) -> None:
    global _config
    unknown = set(kwargs) - {f.name for f in _config.__dataclass_fields__.values()}
    if unknown:
        raise ValueError(f"Unknown config keys: {unknown}")
    for k, v in kwargs.items():
        setattr(_config, k, v)


def get() -> Config:
    if _config.provider is None:
        _config.provider = _auto_detect_provider()
    return _config


def _auto_detect_provider() -> Any:
    from .providers.anthropic import AnthropicProvider
    from .providers.openai import OpenAIProvider

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(model=_config.model)
    elif os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider()
    else:
        raise RuntimeError(
            "cauterize: no AI provider configured. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or pass provider= to cauterize.configure()."
        )


def set_mode(mode: str) -> None:
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode must be 'auto' or 'manual', got {mode!r}")
    _config.mode = mode
