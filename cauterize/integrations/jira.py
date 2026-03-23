from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

from .. import _audit

log = logging.getLogger("cauterize.jira")

if TYPE_CHECKING:
    from .._context import HealContext


@dataclass
class JiraCard:
    url: str
    token: str
    project: str
    email: str = ""          # Atlassian Cloud: email + token -> Basic auth
    issue_type: str = "Task"
    extra_fields: dict[str, Any] = field(default_factory=dict)
    """Extra fields merged into the Jira issue payload.

    Use this for project-required custom fields, e.g.::

        JiraCard(
            url="https://myorg.atlassian.net",
            token="...",
            project="GSE",
            email="me@example.com",
            extra_fields={
                "components": [{"name": "Platform"}],
                "customfield_17684": {"value": "My Team"},
            },
        )
    """

    def create(self, ctx: HealContext) -> str | None:
        """Create a Jira card. Returns the card URL, or None on failure."""
        # Atlassian Cloud uses Basic auth (email:api_token); self-hosted uses Bearer.
        auth = (self.email, self.token) if self.email else None
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if not self.email:
            headers["Authorization"] = f"Bearer {self.token}"
        fields: dict[str, Any] = {
            "project":     {"key": self.project},
            "issuetype":   {"name": self.issue_type},
            "summary":     f"cauterize: {ctx.exc_type} in {ctx.func_qualname}",
            "description": _card_description(ctx),
            "assignee":    None,
        }
        fields.update(self.extra_fields)
        try:
            resp = requests.post(
                f"{self.url}/rest/api/3/issue",
                headers=headers,
                auth=auth,
                json={"fields": fields},
                timeout=10,
            )
            resp.raise_for_status()
            key = resp.json()["key"]
            return f"{self.url}/browse/{key}"
        except Exception as e:
            log.warning("cauterize.jira: card creation failed — %s", e)
            _audit.write_jira_failure(ctx, e)
            return None


def _card_description(ctx: HealContext) -> dict:
    """Build an Atlassian Document Format description for the Jira issue."""
    def text_node(content: str) -> dict:
        return {"type": "text", "text": content}

    def paragraph(*texts) -> dict:
        return {"type": "paragraph", "content": [text_node(t) for t in texts]}

    def code_block(code: str) -> dict:
        return {
            "type": "codeBlock",
            "attrs": {"language": "python"},
            "content": [{"type": "text", "text": code}],
        }

    return {
        "type": "doc",
        "version": 1,
        "content": [
            paragraph(f"cauterize auto-healed this at {ctx.timestamp}. Fix is live in process."),
            paragraph(f"Function: {ctx.func_qualname}"),
            paragraph(f"Exception: {ctx.exc_type}: {ctx.exc_message}"),
            paragraph(f"Confidence: {ctx.confidence:.0%}"),
            paragraph(f"Explanation: {ctx.explanation}"),
            paragraph("Fix:"),
            code_block(ctx.fixed_source),
        ],
    }
