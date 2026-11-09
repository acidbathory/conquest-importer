"""The optional destinations map: which real target a Grafana contact point should go to.

A JSON file the user writes:

    {"version": 1,
     "contact_points": {
       "<contact point name or uid>": {"email": ["oncall@example.com"]},
       "<another>": {"webhook_url": "TEAM_HOOK_URL"},
       "<another>": {"pagerduty_key": "TEAM_PD_KEY"},
       "<another>": {"skip": true}}}

It holds addresses and the NAMES of secrets, never a secret value: a webhook URL or a PagerDuty
key is given as a reference name that the person supplies at apply time (a Terraform sensitive
variable, or `secrets` for the API apply). The file is read strictly: unknown keys, a value that
looks like a URL or a key where a name belongs, a duplicate key, a file over 1 MB or an entry
count over the limit are errors. Error text names the position or key, never a value.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from importer.core.notification_ir import EMAIL_ADDRESS, SecretRef, looks_like_secret
from importer.core.text import printable
from importer.sources.grafana.notifications.models import ContactPoint

MAX_MAP_BYTES = 1_000_000
MAX_ENTRIES = 10_000
MAX_KEY = 200
MAX_ADDRESSES = 150  # the New Relic limit per EMAIL destination on a paid account


class MapError(ValueError):
    """The map cannot be used. The message is fixed text plus a printable key or position."""


def _reference(value: str) -> str:
    """A secret NAME; a URL, an address or anything shaped like a key is refused without echo."""
    if "://" in value or "@" in value or "/" in value or looks_like_secret(value):
        raise ValueError("must be the name of a secret, not the secret itself")
    try:
        SecretRef(name=value)
    except ValidationError:
        raise ValueError(
            "must be a logical secret name: upper case letters, digits and underscores, with "
            "at least one underscore"
        ) from None
    return value


class MapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: list[str] | None = None
    webhook_url: str | None = None
    pagerduty_key: str | None = None
    skip: StrictBool = False

    @field_validator("email")
    @classmethod
    def _addresses(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not 1 <= len(value) <= MAX_ADDRESSES:
            raise ValueError(f"give 1 to {MAX_ADDRESSES} addresses")
        if not all(EMAIL_ADDRESS.match(a) for a in value):
            raise ValueError("not a valid email address")
        return list(dict.fromkeys(value))

    @field_validator("webhook_url", "pagerduty_key")
    @classmethod
    def _names(cls, value: str | None) -> str | None:
        return None if value is None else _reference(value)

    @model_validator(mode="after")
    def _one_target(self) -> "MapEntry":
        chosen = [
            self.email is not None,
            self.webhook_url is not None,
            self.pagerduty_key is not None,
            self.skip,
        ]
        if sum(chosen) != 1:
            raise ValueError("give exactly one of email, webhook_url, pagerduty_key or skip")
        return self


class DestinationMap(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: StrictInt
    contact_points: dict[str, MapEntry]

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("version must be 1")
        return value

    @field_validator("contact_points")
    @classmethod
    def _keys(cls, value: dict[str, MapEntry]) -> dict[str, MapEntry]:
        if len(value) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} entries")
        if any(not k or len(k) > MAX_KEY or not k.isprintable() for k in value):
            raise ValueError("a key is empty, too long or not printable text")
        return value

    def entry_for(self, cp: ContactPoint) -> MapEntry | None:
        """The entry for a contact point: its uid first, then its name."""
        if cp.uid and cp.uid in self.contact_points:
            return self.contact_points[cp.uid]
        return self.contact_points.get(cp.name)

    def unused(self, points: list[ContactPoint]) -> int:
        """How many entries matched no contact point."""
        used = {k for cp in points for k in (cp.uid, cp.name) if k in self.contact_points}
        return len(set(self.contact_points) - used)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise MapError(
            "the map has a key twice: " + printable(next(k for k in keys if keys.count(k) > 1), 60)
        )
    return dict(pairs)


def _problem(exc: ValidationError) -> str:
    """Positions and fixed messages only: pydantic's text can quote the input."""
    first = exc.errors(include_input=False, include_url=False)[0]
    where = ".".join(printable(str(p), 60) for p in first["loc"]) or "the file"
    return f"{where}: {printable(first['msg'], 100)}"


def parse_map(text: str) -> DestinationMap:
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise MapError("the destinations map is nested too deeply") from None
    except json.JSONDecodeError:
        raise MapError("the destinations map is not JSON") from None
    try:
        return DestinationMap.model_validate(raw)
    except ValidationError as exc:
        raise MapError("destinations map: " + _problem(exc)) from None


def load_map(path: Path) -> DestinationMap:
    if not path.is_file():
        raise MapError("the destinations map is not a file")
    if path.stat().st_size > MAX_MAP_BYTES:
        raise MapError(f"the destinations map is larger than {MAX_MAP_BYTES // 1000} KB")
    return parse_map(path.read_text())
