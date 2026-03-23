"""
GitHub integration: opens a pull request with the healed function source.

Configure::

    import cauterize
    cauterize.configure(
        github=cauterize.GitHubPR(
            token="ghp_...",
            repo="owner/repo",
            base_branch="main",
        )
    )
"""
from __future__ import annotations

import base64
import logging
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from cauterize._context import HealContext

log = logging.getLogger("cauterize.github")

_API = "https://api.github.com"


class GitHubPR:
    def __init__(self, token: str, repo: str, base_branch: str = "main") -> None:
        self.token = token
        self.repo = repo          # "owner/repo"
        self.base_branch = base_branch
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def create(self, ctx: HealContext) -> str | None:
        """Open a PR with the healed source. Returns PR URL or None on failure."""
        try:
            return self._create(ctx)
        except Exception as e:
            log.warning("cauterize.github: PR creation failed — %s", e)
            return None

    def _create(self, ctx: HealContext) -> str | None:
        if not ctx.source_file or not ctx.original_source or not ctx.fixed_source:
            log.warning("cauterize.github: missing source context, cannot create PR")
            return None

        # Get base branch SHA
        ref_resp = self._session.get(f"{_API}/repos/{self.repo}/branches/{self.base_branch}")
        if ref_resp.status_code != 200:
            log.warning("cauterize.github: could not get base branch — %s %s", ref_resp.status_code, ref_resp.text[:200])
            return None
        base_sha = ref_resp.json()["commit"]["sha"]

        # Create heal branch
        func_slug = ctx.func_qualname.split(".")[-1].replace("_", "-")
        ts = int(time.time())
        branch_name = f"cauterize/heal-{func_slug}-{ts}"

        branch_resp = self._session.post(f"{_API}/repos/{self.repo}/git/refs", json={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha,
        })
        if branch_resp.status_code not in (200, 201):
            log.warning("cauterize.github: could not create branch — %s %s", branch_resp.status_code, branch_resp.text[:200])
            return None

        # Get file content and SHA (needed for update)
        # Determine relative path from repo root — use just the filename for simplicity
        import os
        # Try to find the file relative to the repo root by looking for a git root
        file_path = _repo_relative_path(self.repo, ctx.source_file)
        if not file_path:
            log.warning("cauterize.github: could not determine repo-relative path for %s", ctx.source_file)
            return None

        file_resp = self._session.get(
            f"{_API}/repos/{self.repo}/contents/{file_path}",
            params={"ref": self.base_branch},
        )
        if file_resp.status_code != 200:
            log.warning("cauterize.github: could not get file contents — %s %s", file_resp.status_code, file_resp.text[:200])
            return None

        file_data = file_resp.json()
        file_sha = file_data["sha"]
        current_content = base64.b64decode(file_data["content"]).decode("utf-8")

        # Replace original source with fixed source
        if ctx.original_source not in current_content:
            log.warning("cauterize.github: original source not found in file, cannot create PR")
            return None

        new_content = current_content.replace(ctx.original_source, ctx.fixed_source, 1)
        encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")

        update_resp = self._session.put(
            f"{_API}/repos/{self.repo}/contents/{file_path}",
            json={
                "message": f"fix: cauterize healed {ctx.func_qualname}\n\n{ctx.explanation}",
                "content": encoded,
                "sha": file_sha,
                "branch": branch_name,
            },
        )
        if update_resp.status_code not in (200, 201):
            log.warning("cauterize.github: could not commit fix — %s %s", update_resp.status_code, update_resp.text[:200])
            return None

        # Open PR
        pr_resp = self._session.post(f"{_API}/repos/{self.repo}/pulls", json={
            "title": f"fix: cauterize healed {ctx.func_qualname}",
            "body": (
                f"## Automated fix by [cauterize](https://github.com/ferndot/cauterize)\n\n"
                f"**Function:** `{ctx.func_qualname}`\n"
                f"**Exception:** `{ctx.exc_type}: {ctx.exc_message}`\n"
                f"**Confidence:** {ctx.confidence:.0%}\n\n"
                f"**Explanation:** {ctx.explanation}\n\n"
                f"---\n"
                f"*This PR was opened automatically when cauterize healed a runtime exception.*"
            ),
            "head": branch_name,
            "base": self.base_branch,
        })
        if pr_resp.status_code not in (200, 201):
            log.warning("cauterize.github: could not create PR — %s %s", pr_resp.status_code, pr_resp.text[:200])
            return None

        pr_url = pr_resp.json().get("html_url")
        log.info("cauterize.github: PR opened — %s", pr_url)
        return pr_url


def _repo_relative_path(repo: str, absolute_path: str) -> str | None:
    """Attempt to find the path relative to the repo root by walking up for .git."""
    import os
    from pathlib import Path

    p = Path(absolute_path)
    for parent in p.parents:
        if (parent / ".git").exists():
            try:
                return str(p.relative_to(parent))
            except ValueError:
                return None
    return None
