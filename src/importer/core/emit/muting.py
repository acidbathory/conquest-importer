"""Emit muting rules as NerdGraph variables and Terraform (`newrelic_alert_muting_rule`).

Names, values and descriptions are untrusted: NerdGraph values travel as GraphQL variables and
Terraform strings go through hcl_string(). The GraphQL document is a constant. The NerdGraph input
field names follow New Relic's muting rule page and are UNVERIFIED against a live account.
"""

from typing import Any

from importer.core.emit.nerdgraph import ACCOUNT_PLACEHOLDER
from importer.core.emit.terraform import hcl_string
from importer.core.muting_ir import MutingRule, MutingSchedule

MUTATION = (
    "mutation CreateMutingRule($accountId: Int!, $rule: AlertsMutingRuleInput!) {\n"
    "  alertsMutingRuleCreate(accountId: $accountId, rule: $rule) {\n"
    "    id\n    name\n  }\n}"
)


def _schedule_input(schedule: MutingSchedule) -> dict[str, Any]:
    out: dict[str, Any] = {
        "startTime": schedule.start_time,
        "endTime": schedule.end_time,
        "timeZone": schedule.time_zone,
    }
    if schedule.repeat is not None:
        out["repeat"] = schedule.repeat.value
    if schedule.end_repeat is not None:
        out["endRepeat"] = schedule.end_repeat
    if schedule.repeat_count is not None:
        out["repeatCount"] = schedule.repeat_count
    if schedule.weekly_repeat_days:
        out["weeklyRepeatDays"] = [d.value for d in schedule.weekly_repeat_days]
    return out


def muting_input(rule: MutingRule) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.enabled,
        "condition": {
            "operator": rule.condition.operator,
            "conditions": [
                {"attribute": c.attribute, "operator": c.operator.value, "values": c.values}
                for c in rule.condition.conditions
            ],
        },
    }
    if rule.schedule is not None:
        body["schedule"] = _schedule_input(rule.schedule)
    if rule.action_on_window_ended is not None:
        body["actionOnMutingRuleWindowEnded"] = rule.action_on_window_ended
    return body


def muting_payload(rule: MutingRule) -> dict[str, Any]:
    return {
        "query": MUTATION,
        "variables": {"accountId": ACCOUNT_PLACEHOLDER, "rule": muting_input(rule)},
    }


def _schedule_block(schedule: MutingSchedule) -> str:
    lines = [
        f"    start_time = {hcl_string(schedule.start_time)}",
        f"    end_time   = {hcl_string(schedule.end_time)}",
        f"    time_zone  = {hcl_string(schedule.time_zone)}",
    ]
    if schedule.repeat is not None:
        lines.append(f'    repeat     = "{schedule.repeat.value}"')
    if schedule.end_repeat is not None:
        lines.append(f"    end_repeat = {hcl_string(schedule.end_repeat)}")
    if schedule.repeat_count is not None:
        lines.append(f"    repeat_count = {schedule.repeat_count}")
    if schedule.weekly_repeat_days:
        days = ", ".join(f'"{d.value}"' for d in schedule.weekly_repeat_days)
        lines.append(f"    weekly_repeat_days = [{days}]")
    return "\n  schedule {\n" + "\n".join(lines) + "\n  }\n"


def _rule_block(rule: MutingRule, n: int) -> str:
    conditions = "".join(
        f"\n    conditions {{\n      attribute = {hcl_string(c.attribute)}\n"
        f'      operator  = "{c.operator.value}"\n'
        f"      values    = [{', '.join(hcl_string(v) for v in c.values)}]\n    }}\n"
        for c in rule.condition.conditions
    )
    action = (
        f'  action_on_muting_rule_window_ended = "{rule.action_on_window_ended}"\n'
        if rule.action_on_window_ended
        else ""
    )
    schedule = _schedule_block(rule.schedule) if rule.schedule is not None else ""
    return (
        f'resource "newrelic_alert_muting_rule" "m_{n}" {{\n'
        f"  name        = {hcl_string(rule.name)}\n"
        f"  description = {hcl_string(rule.description)}\n"
        f"  enabled     = {'true' if rule.enabled else 'false'}\n"
        f"{action}\n"
        f'  condition {{\n    operator = "{rule.condition.operator}"\n{conditions}  }}\n'
        f"{schedule}}}\n"
    )


def muting_terraform(rules: list[MutingRule]) -> str:
    """One `newrelic_alert_muting_rule` per rule, disabled unless the rule says otherwise."""
    return "\n".join(_rule_block(rule, n) for n, rule in enumerate(rules))
