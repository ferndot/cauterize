from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .._context import HealContext


@dataclass
class SlackNotifier:
    webhook_url: str

    def send(
        self,
        ctx: HealContext,
        card_url: str | None,
        github_pr_url: str | None = None,
    ) -> None:
        lines = [
            f"🩹 *cauterize* healed `{ctx.func_qualname}`",
            f"*Exception:* `{ctx.exc_type}: {ctx.exc_message}`",
            f"*Confidence:* {ctx.confidence:.0%}",
        ]

        links: list[str] = []
        if card_url:
            links.append(f"<{card_url}|Jira>")
        if github_pr_url:
            links.append(f"<{github_pr_url}|Pull Request>")

        if links:
            lines.append(" | ".join(links))
        else:
            lines.append("_No Jira card or PR created._")

        requests.post(
            self.webhook_url,
            json={"text": "\n".join(lines)},
            timeout=5,
        )
