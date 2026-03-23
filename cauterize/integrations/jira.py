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

    def _auth(self) -> tuple[tuple[str, str] | None, dict[str, str]]:
        auth = (self.email, self.token) if self.email else None
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if not self.email:
            headers["Authorization"] = f"Bearer {self.token}"
        return auth, headers

    @staticmethod
    def _dedup_label(ctx: HealContext) -> str:
        """Deterministic label used as an exact-match dedup key."""
        return f"cauterize:{ctx.func_qualname}:{ctx.exc_type}"

    def _find_existing(self, ctx: HealContext) -> str | None:
        """Find an open issue with the dedup label. Returns URL or None."""
        auth, headers = self._auth()
        label = self._dedup_label(ctx)
        jql = f'project = {self.project} AND labels = "{label}" AND status != Done'
        try:
            resp = requests.get(
                f"{self.url}/rest/api/3/search",
                headers=headers,
                auth=auth,
                params={"jql": jql, "fields": "key", "maxResults": 1},
                timeout=10,
            )
            if resp.ok and resp.json().get("total", 0) > 0:
                key = resp.json()["issues"][0]["key"]
                log.info("cauterize.jira: reusing existing card %s", key)
                return f"{self.url}/browse/{key}"
        except Exception as e:
            log.warning("cauterize.jira: dedup search failed — %s", e)
        return None

    def create(self, ctx: HealContext) -> str | None:
        """Create a Jira card, or return the existing one if already created."""
        existing = self._find_existing(ctx)
        if existing:
            return existing

        auth, headers = self._auth()
        fields: dict[str, Any] = {
            "project":     {"key": self.project},
            "issuetype":   {"name": self.issue_type},
            "summary":     f"cauterize: {ctx.exc_type} in {ctx.func_qualname}",
            "description": _card_description(ctx),
            "assignee":    None,
            "labels":      [self._dedup_label(ctx)],
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
