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

    def _t(text: str, **marks) -> dict:
        node: dict = {"type": "text", "text": text}
        if marks:
            node["marks"] = [{"type": k} for k in marks]
        return node

    def _p(*children) -> dict:
        return {"type": "paragraph", "content": list(children)}

    def _heading(text: str, level: int = 3) -> dict:
        return {"type": "heading", "attrs": {"level": level}, "content": [_t(text)]}

    def _code(text: str, lang: str = "python") -> dict:
        return {"type": "codeBlock", "attrs": {"language": lang}, "content": [_t(text)]}

    def _rule() -> dict:
        return {"type": "rule"}

    def _panel(content: list, panel_type: str = "info") -> dict:
        return {"type": "panel", "attrs": {"panelType": panel_type}, "content": content}

    return {
        "type": "doc",
        "version": 1,
        "content": [
            _panel([
                _p(_t("cauterize ", strong=True), _t(f"auto-healed this function at {ctx.timestamp}. The fix is live in-process — this card tracks the code change for review.")),
            ], "info"),

            _heading("Exception"),
            _p(_t(f"{ctx.exc_type}: ", strong=True), _t(ctx.exc_message)),
            _p(_t("Function: ", strong=True), _t(ctx.func_qualname, code=True)),
            _p(_t("Confidence: ", strong=True), _t(f"{ctx.confidence:.0%}")),

            _rule(),

            _heading("Explanation"),
            _p(_t(ctx.explanation)),

            _heading("Patch"),
            _code(ctx.fixed_source),
        ],
    }
