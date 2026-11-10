"""channels.yaml: who is who across platforms (identities), for the relay's commands and the Mandate;
and the watchdog's settings (myorch/watchdog.py)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ChannelsConfig(BaseModel):
    identities: dict[str, dict[str, str]] = Field(default_factory=dict)  # person -> {channel: account}
    watchdog: dict = Field(default_factory=dict)  # settings for myorch/watchdog.py (WatchdogConfig); empty: defaults

    def person(self, channel: str, account: str) -> str:
        """The person behind a channel account (falls back to the account itself)."""
        for person, accounts in self.identities.items():
            if accounts.get(channel, "").lower() == account.lower():
                return person
        return account

    def aliases(self, person: str) -> list[str]:
        """Every account of a person, for the Mandate (approvals from any of them count as theirs)."""
        return sorted({a for a in self.identities.get(person, {}).values() if a and a != person})


def load_config(path: Path) -> ChannelsConfig:
    raw = (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}
    return ChannelsConfig(identities=raw.get("identities") or {}, watchdog=raw.get("watchdog") or {})
