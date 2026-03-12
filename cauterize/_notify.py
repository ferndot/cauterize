from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from ._context import HealContext


@dataclass
class SlackNotifier:
    webhook_url: str

    def send(self, ctx: HealContext, card_url: str | None) -> None:
        card_link = f"<{card_url}|Jira card>" if card_url else "no card created"
        requests.post(
            self.webhook_url,
            json={
                "text": (
                    f":bandage: *cauterize* healed `{ctx.func_qualname}` "
                    f"\u2014 {ctx.exc_type} ({ctx.confidence:.0%} confidence) "
                    f"\u2014 {card_link}"
                )
            },
            timeout=5,
        )
