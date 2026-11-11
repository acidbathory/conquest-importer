"""Policy and condition fields beyond the query: severity, alert strategy, documentation, labels,
missing-data handling and forecasts, mapped to New Relic settings or to a precise note.

Everything that comes from the policy is untrusted text. It reaches New Relic only through the
emitters (a GraphQL variable or `hcl_string`) and the report (`md_cell`); here it is cut to a
printable, bounded form so a hostile label cannot flood a description.
"""

from dataclasses import dataclass, field
from typing import Any

from importer.core.emit.common import RUNBOOK_URL, VIOLATION_TIME_LIMIT_SECONDS
from importer.core.ir import EmitOptions, NoData, Severity
from importer.core.text import printable

FIELD_LIMIT = 200  # characters kept from any one untrusted value
MAX_ITEMS = 10  # labels, links or extractors listed in a description
HOURS = 3600
FORECAST_SECONDS = (1 * HOURS, 60 * HOURS)  # Google's documented range for forecastHorizon


def seconds(value: object) -> int:
    """Whole seconds in a Google duration such as "3600s"; 0 when it is not one."""
    text = str(value or "")
    digits = text[:-1]
    return int(digits) if text.endswith("s") and digits.isascii() and digits.isdecimal() else 0


def _clean(value: object) -> str:
    return printable(str(value), FIELD_LIMIT)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def severity_for(policy_severity: str) -> tuple[Severity, str | None]:
    """New Relic has critical and warning. ERROR has no level of its own, so it is critical."""
    if policy_severity == "WARNING":
        return Severity.WARNING, None
    if policy_severity == "ERROR":
        return Severity.CRITICAL, (
            "the Google policy severity is ERROR; New Relic has only critical and warning, so it "
            "is critical here (set the threshold to warning if ERROR should page less urgently)"
        )
    if policy_severity in ("", "CRITICAL", "SEVERITY_UNSPECIFIED"):
        return Severity.CRITICAL, None
    return Severity.CRITICAL, (
        f"the Google policy severity {_clean(policy_severity)!r} is not one New Relic knows; "
        "it is critical here"
    )


@dataclass
class Settings:
    """What the fields of one condition add to the monitor."""

    emit: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _violation_limit(period: int) -> int | None:
    low, high = VIOLATION_TIME_LIMIT_SECONDS
    return period if low <= period <= high else None


def _strategy(strategy: dict[str, Any], *, log: bool, settings: Settings) -> None:
    """alertStrategy: auto-close or the rate limit becomes the violation time limit."""
    close = seconds(strategy.get("autoClose"))
    rate = seconds(_dict(strategy.get("notificationRateLimit")).get("period"))
    notes = settings.notes
    if log:
        limit = _violation_limit(rate) if rate else None
        if limit:
            settings.emit["violation_time_limit_seconds"] = limit
            notes.append(
                f"Google notifies for a matching log at most once every {rate}s; New Relic has no "
                f"notification rate limit, so the violation time limit is set to {limit}s: a "
                "violation that keeps matching is closed and opened again after that time, which "
                "notifies again no more often than the Google limit"
            )
            if close:
                notes.append(
                    f"autoClose of {close}s is not mapped: the violation time limit carries the "
                    "rate limit instead"
                )
        else:
            notes.append(
                "Google requires a notification rate limit on log alerts and this policy has "
                f"none that New Relic can use ({rate}s); a log match opens one issue for as long "
                "as matches continue"
                if rate
                else "this log alert has no notificationRateLimit, which Google requires"
            )
            _auto_close(close, settings)
    else:
        _auto_close(close, settings)
        if rate:
            notes.append(
                f"a notification rate limit of {rate}s has no equivalent for a metric condition; "
                "New Relic opens one issue per violation and does not repeat notifications"
            )
    prompts = strategy.get("notificationPrompts")
    if isinstance(prompts, list) and prompts:
        names = ", ".join(sorted(_clean(p) for p in prompts[:MAX_ITEMS]))
        notes.append(
            f"Google sends notifications when an incident is {names}; in New Relic set the "
            "workflow to notify on 'issue activated' (opened) and 'issue closed' (closed)"
        )
    renotify = strategy.get("notificationChannelStrategy")
    if isinstance(renotify, list) and renotify:
        intervals = sorted({_clean(_dict(r).get("renotifyInterval", "")) for r in renotify})
        notes.append(
            f"Google repeats notifications every {', '.join(intervals)} while the incident is "
            "open; New Relic workflows notify on issue changes and have no repeat reminder, "
            "so repeat reminders must be rebuilt outside the condition"
        )


def _auto_close(close: int, settings: Settings) -> None:
    if not close:
        return
    limit = _violation_limit(close)
    if limit:
        settings.emit["violation_time_limit_seconds"] = limit
        settings.notes.append(
            f"Google closes the incident after {close}s without a new violation (autoClose); "
            f"the violation time limit is {limit}s, which closes the violation even if the "
            "signal is still breaching"
        )
    else:
        settings.notes.append(
            f"autoClose of {close}s is outside the 5 minute to 30 day violation time limit "
            "New Relic accepts, so it is not set"
        )


def _links(documentation: dict[str, Any]) -> list[tuple[str, str]]:
    raw = documentation.get("links")
    links = []
    for item in raw[:MAX_ITEMS] if isinstance(raw, list) else []:
        entry = _dict(item)
        if entry.get("url"):
            links.append((_clean(entry.get("displayName", "")), _clean(entry["url"])))
    return links


def _description(
    policy: Any, body: dict[str, Any], strategy: dict[str, Any], links: list[tuple[str, str]]
) -> str:
    """Lines for the condition description: the fields that would otherwise be lost."""
    doc = _dict(policy.documentation)
    lines: list[str] = []
    if policy.severity:
        lines.append(f"Google severity: {_clean(policy.severity)}")
    if doc.get("subject"):
        lines.append(f"Notification subject: {_clean(doc['subject'])}")
    if doc.get("mimeType"):
        lines.append(f"Documentation format: {_clean(doc['mimeType'])}")
    for name, url in links:
        lines.append(f"Link: {name} {url}".replace("  ", " "))
    labels = policy.userLabels
    if labels:
        shown = ", ".join(f"{_clean(k)}={_clean(v)}" for k, v in list(labels.items())[:MAX_ITEMS])
        lines.append(f"Google labels: {shown}")
    extractors = body.get("labelExtractors")
    if isinstance(extractors, dict) and extractors:
        shown = "; ".join(
            f"{_clean(k)}: {_clean(v)}" for k, v in list(extractors.items())[:MAX_ITEMS]
        )
        lines.append(f"Google log label extractors: {shown}")
    parts = []
    if seconds(_dict(strategy.get("notificationRateLimit")).get("period")):
        parts.append("rate limit " + _clean(strategy["notificationRateLimit"]["period"]))
    if seconds(strategy.get("autoClose")):
        parts.append("autoClose " + _clean(strategy["autoClose"]))
    renotify = strategy.get("notificationChannelStrategy")
    if isinstance(renotify, list) and renotify:
        parts.append(
            "renotify "
            + ", ".join(sorted({_clean(_dict(r).get("renotifyInterval", "")) for r in renotify}))
        )
    if parts:
        lines.append("Google alert strategy: " + "; ".join(parts))
    return "\n".join(lines)


def _tags(policy: Any) -> list[str]:
    tags = []
    if policy.severity:
        tags.append(f"gcp-severity:{_clean(policy.severity)}")
    if policy.userLabels:
        tags += [
            f"{_clean(k)}:{_clean(v)}" for k, v in list(policy.userLabels.items())[: MAX_ITEMS * 3]
        ]
    return tags


def carry_fields(policy: Any, body: dict[str, Any], *, log: bool) -> Settings:
    """Preserved fields for one condition: emit options, notes and tags."""
    settings = Settings()
    strategy = _dict(policy.alertStrategy)
    _strategy(strategy, log=log, settings=settings)
    links = _links(_dict(policy.documentation))
    for _, url in links:
        if RUNBOOK_URL.fullmatch(url) and "${" not in url:
            settings.emit["runbook_url"] = url
            break
    extractors = body.get("labelExtractors")
    if isinstance(extractors, dict) and extractors:
        settings.notes.append(
            "labelExtractors fill labels on the Google incident from the log entry; New Relic has "
            "no equivalent on a condition, so they are listed in the description: add the "
            "attributes to the issue title template or to a FACET on the log query"
        )
    settings.tags = _tags(policy)
    settings.emit["description_note"] = _description(policy, body, strategy, links)
    return settings


def missing_data(value: str, duration: int) -> tuple[NoData, dict[str, Any], str | None]:
    """evaluationMissingData as loss-of-signal settings, extra emit options and a note."""
    minutes = max(1, duration // 60)
    if value.endswith("_ACTIVE"):
        return (
            NoData(notify=True, timeout_minutes=minutes),
            {},
            "EVALUATION_MISSING_DATA_ACTIVE becomes loss of signal after the duration: New "
            "Relic opens a violation when no data arrives, as Google treats missing data as a "
            "breach (Google decides per series, New Relic per query)",
        )
    if value.endswith("_INACTIVE"):
        return (
            NoData(notify=True, timeout_minutes=minutes),
            {"open_violation_on_expiration": False, "close_violations_on_expiration": True},
            "EVALUATION_MISSING_DATA_INACTIVE becomes loss of signal that closes open "
            "violations after the duration; it opens none. Google treats missing data as not "
            "breaching from the first missing point, New Relic waits for the duration",
        )
    if value.endswith("_NO_OP"):
        return (
            NoData(),
            {},
            "EVALUATION_MISSING_DATA_NO_OP keeps the incident as it is while data is missing; "
            "New Relic does the same without a loss-of-signal setting (the violation stays "
            "open until the violation time limit)",
        )
    return NoData(), {}, None


def forecast(body: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
    """forecastOptions as the predict-future option: (emit options, note, reason to stay manual)."""
    options = body.get("forecastOptions")
    if not options:
        return {}, None, None
    horizon = seconds(_dict(options).get("forecastHorizon"))
    low, high = FORECAST_SECONDS
    if not low <= horizon <= high:
        return {}, None, f"forecast horizon {horizon}s is outside Google's 1 to 60 hours"
    return (
        {"predict_by_seconds": horizon},
        f"a forecast condition becomes a static condition with New Relic's predict-future option "
        f"({horizon}s ahead; the option is in beta). New Relic extrapolates the recent signal, "
        "which is not the same model as Google's forecast, so compare the two before trusting it",
        None,
    )


def emit_options(settings: Settings, extra: dict[str, Any]) -> EmitOptions | None:
    merged = {**settings.emit, **extra}
    if not merged.get("description_note"):
        merged.pop("description_note", None)
    return EmitOptions.model_validate(merged) if merged else None
