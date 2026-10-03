"""Helpers for showing untrusted text safely."""


def printable(text: str, limit: int = 80) -> str:
    """Make untrusted text safe to print to a terminal (no control or escape characters)."""
    return "".join(c if c.isprintable() else "?" for c in text)[:limit]
