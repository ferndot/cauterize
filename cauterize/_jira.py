from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from . import _audit

if TYPE_CHECKING:
    from ._context import HealContext


@dataclass
class JiraCard:
    url: str
    token: str
    project: str
    issue_type: str = "Task"

    def create(self, ctx: HealContext) -> str | None:
        """Create a Jira card. Returns the card URL, or None on failure."""
        try:
            resp = requests.post(
                f"{self.url}/rest/api/3/issue",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "fields": {
                        "project":   {"key": self.project},
                        "issuetype": {"name": self.issue_type},
                        "summary":   f"cauterize: {ctx.exc_type} in {ctx.func_qualname}",
                        "description": _card_description(ctx),
                    }
                },
                timeout=10,
            )
            resp.raise_for_status()
            key = resp.json()["key"]
            return f"{self.url}/browse/{key}"
        except Exception as e:
            _audit.write_jira_failure(ctx, e)
            return None


def _card_description(ctx: HealContext) -> str:
    return (
        f"cauterize auto-healed this at {ctx.timestamp}. Fix is live in process.\n\n"
        f"*Function:* {ctx.func_qualname}\n"
        f"*Exception:* {ctx.exc_type}: {ctx.exc_message}\n"
        f"*Confidence:* {ctx.confidence:.0%}\n"
        f"*Explanation:* {ctx.explanation}\n\n"
        f"*Fix:*\n"
        f"{{code:python}}\n"
        f"{ctx.fixed_source}\n"
        f"{{code}}"
    )
