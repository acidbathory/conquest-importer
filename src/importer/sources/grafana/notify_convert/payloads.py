"""The webhook payload templates of the WEBHOOK TEMPLATE contact point types, from a data file.

`payload_templates.json` holds one entry per type: the Handlebars body (lines of text), the
fixed part of the URL, how the service is authenticated, placeholders a person must replace and
the sentences saying what is unverified. The file is checked when it is read: every variable
must be a real New Relic variable, values may reach a JSON body only through `json` or `escape`,
and the placeholders listed must be the ones in the body. Nothing in the file comes from an
export.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from importer.sources.grafana.notify_convert import handlebars as hb

TEMPLATE_FILE = Path(__file__).with_name("payload_templates.json")
PLACEHOLDER = re.compile(r"REPLACE_[A-Z_]+")


class UrlSpec(BaseModel):
    plain: str = ""  # a URL with no secret: used as is
    host_option: str = ""  # the plain URL with a host the contact point named; {host} is replaced
    prefix: str = ""  # the fixed start of a URL whose rest is secret
    secret: str = ""  # what the secret rest of the URL is, as a sentence for the person


class AuthSpec(BaseModel):
    kind: Literal["token", "header"]
    prefix: str = ""  # token: the prefix New Relic puts before the token
    key: str = ""  # header: the header name
    scheme: str = ""  # header: the word before the key in the value, such as GenieKey


class PayloadTemplate(BaseModel):
    status: Literal["documented", "unverified"]
    summary: str
    url: UrlSpec | None
    auth: AuthSpec | None
    channel_headers: dict[str, str] | None
    placeholders: dict[str, str]
    unverified: list[str]
    body_lines: list[str]

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines)


def _check(kind: str, template: PayloadTemplate) -> None:
    problems = [*hb.check(template.body), *hb.json_position_problems(template.body)]
    found = set(PLACEHOLDER.findall(template.body))
    if found != set(template.placeholders):
        problems.append("placeholders listed differ from those in the body")
    if problems:
        raise ValueError(f"payload template {kind}: {'; '.join(problems)}")


def parse_templates(raw: dict[str, dict[str, Any]]) -> dict[str, PayloadTemplate]:
    found: dict[str, PayloadTemplate] = {}
    for kind, entry in raw.items():
        fields = {("body_lines" if k == "body" else k): v for k, v in entry.items()}
        template = PayloadTemplate.model_validate(fields)
        _check(kind, template)
        found[kind] = template
    return found


@lru_cache(maxsize=1)
def all_templates() -> dict[str, PayloadTemplate]:
    return parse_templates(json.loads(TEMPLATE_FILE.read_text())["templates"])


def template_for(kind: str) -> PayloadTemplate:
    return all_templates()[kind]
