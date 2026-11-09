"""Shared safeguards for the read-only source extractors that call a vendor API with a token.

What every such getter needs: no redirects followed (so the token cannot reach another host), a
status of 300 or more is an error, server text is scrubbed of the token (also in URL-encoded
form) and any bearer value and capped before it reaches a message, each response is streamed
against a size cap, the whole run has a byte cap and a wall-clock deadline, and a connection
failure names only the exception type. Each source passes its own error class and service name.

Status: used by the Google extractor. The Dynatrace extractor still has its own copy of these
pieces and is to move to this module when the branches are merged.
"""

import json
import re
import time
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

from importer.core.json_depth import TooDeepError, check_depth
from importer.core.text import printable

BEARER = re.compile(r"Bearer[:\s]+\S+", re.IGNORECASE)
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # across every response of one run
DEADLINE_SECONDS = 600  # for a whole run; httpx timeouts are per operation and a drip never ends
MAX_MESSAGE = 300
_clock = time.monotonic  # replaced in tests


class ReadBudget:
    """Bytes and time left for a whole run, shared by every request made with one getter."""

    def __init__(self) -> None:
        self.bytes_left = MAX_TOTAL_BYTES
        self.until = _clock() + DEADLINE_SECONDS

    def check_time(self, error: type[Exception], service: str) -> None:
        if _clock() > self.until:
            raise error(f"reading {service} took longer than {DEADLINE_SECONDS} seconds; stopped")

    def spend(self, size: int, error: type[Exception]) -> None:
        self.bytes_left -= size
        if self.bytes_left < 0:
            raise error(f"more than {MAX_TOTAL_BYTES} bytes in total; stopped")


def is_plain_token(token: str) -> bool:
    """True for a non-empty token of printable ASCII with no spaces (a smart quote fails this)."""
    return bool(token) and token.isascii() and token.isprintable() and " " not in token


def scrub(text: str, token: str) -> str:
    """Server text that is safe to show: no token (plain or URL-encoded), no bearer value."""
    if token:
        forms = {token, quote(token), quote(token, safe=""), quote_plus(token)}
        for form in sorted(forms, key=len, reverse=True):
            text = text.replace(form, "***")
    return printable(BEARER.sub("Bearer ***", text), MAX_MESSAGE)


def error_message(body: object) -> str:
    """The error message in a response body, or "" when the body has another shape."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    found = error.get("message") if isinstance(error, dict) else error or body.get("message")
    return found if isinstance(found, str) else ""


def read_body(
    response: httpx.Response,
    error: type[Exception],
    service: str,
    budget: ReadBudget | None = None,
) -> bytes:
    """The body, refusing one larger than MAX_RESPONSE_BYTES or past the run's budget."""
    too_big = f"{service} sent a response larger than the importer accepts"
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise error(too_big)
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise error(too_big)
        if budget is not None:
            budget.check_time(error, service)
            budget.spend(len(chunk), error)
        chunks.append(chunk)
    return b"".join(chunks)


def decode_json(
    response: httpx.Response,
    token: str,
    error: type[Exception],
    service: str,
    budget: ReadBudget | None = None,
) -> dict[str, Any]:
    """The response as a JSON object; any status of 300 or more raises `error`."""
    body = read_body(response, error, service, budget)
    valid, nested = True, False
    try:
        check_depth(body)
        parsed = json.loads(body)
    except TooDeepError:
        parsed, valid, nested = None, False, True
    except (ValueError, RecursionError):
        parsed, valid = None, False
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = scrub(error_message(parsed), token)
        raise error(f"{service} returned {status}" + (f": {detail}" if detail else ""))
    if nested:
        raise error(f"{service} sent a response nested too deeply to read")
    if not valid:
        raise error(f"{service} returned a body that is not JSON")
    if not isinstance(parsed, dict):
        raise error(f"{service} returned JSON that is not a JSON object")
    return parsed


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    token: str,
    error: type[Exception],
    service: str,
    budget: ReadBudget | None = None,
) -> dict[str, Any]:
    """One streamed GET. Network errors become `error` naming only the exception type."""
    if budget is not None:
        budget.check_time(error, service)
    try:
        with client.stream("GET", url, params=params) as response:
            return decode_json(response, token, error, service, budget)
    except httpx.HTTPError as exc:
        raise error(f"could not reach {service}: {type(exc).__name__}") from None
