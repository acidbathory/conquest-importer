"""Datadog source models: the raw shapes we receive, validated but not interpreted.

Unknown fields are kept (extra="allow") so nothing is silently dropped; the
translator decides what to do with them and reports anything it cannot map.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class DatadogMonitor(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    type: str
    query: str
    message: str = ""
    tags: list[str] = []
    options: dict[str, Any] = {}
    overall_state: str | None = None
    priority: int | None = None
    created: str | None = None
    modified: str | None = None
