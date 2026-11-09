"""Shared safeguards for the read-only source extractors that call a vendor API with a token.

What every such getter needs: no redirects followed (so the token cannot reach another host), a
status of 300 or more is an error, server text is scrubbed of the token and any bearer value and
capped before it reaches a message, the response is streamed against a size cap, and a connection
failure names only the exception type. Each source passes its own error class and service name.
"""

import json
import re
from typing import Any

import httpx

from importer.core.text import printable

BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE = 300


def is_plain_token(token: str) -> bool:
    """True for a non-empty token of printable ASCII with no spaces (a smart quote fails this)."""
    return bool(token) and token.isascii() and token.isprintable() and " " not in token


def scrub(text: str, token: str) -> str:
    """Server text that is safe to show: no token, no bearer value, printable, capped."""
    if token:
        text = text.replace(token, "***")
    return printable(BEARER.sub("Bearer ***", text), MAX_MESSAGE)


def error_message(body: object) -> str:
    """The error message in a response body, or "" when the body has another shape."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    found = error.get("message") if isinstance(error, dict) else error or body.get("message")
    return found if isinstance(found, str) else ""


def read_body(response: httpx.Response, error: type[Exception], service: str) -> bytes:
    """The body, refusing one larger than MAX_RESPONSE_BYTES (header or actual size)."""
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
        chunks.append(chunk)
    return b"".join(chunks)


def decode_json(
    response: httpx.Response, token: str, error: type[Exception], service: str
) -> dict[str, Any]:
    """The response as a JSON object; any status of 300 or more raises `error`."""
    body = read_body(response, error, service)
    valid = True
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed, valid = None, False
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = scrub(error_message(parsed), token) if status >= 400 else ""
        raise error(f"{service} returned {status}" + (f": {detail}" if detail else ""))
    if not valid:
        raise error(f"{service} returned a body that is not JSON")
    return parsed if isinstance(parsed, dict) else {}


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    token: str,
    error: type[Exception],
    service: str,
) -> dict[str, Any]:
    """One streamed GET. Network errors become `error` naming only the exception type."""
    try:
        with client.stream("GET", url, params=params) as response:
            return decode_json(response, token, error, service)
    except httpx.HTTPError as exc:
        raise error(f"could not reach {service}: {type(exc).__name__}") from None
