"""The webhook body for Azure receivers: New Relic variables laid out like the common alert schema.

The Azure common alert schema (`schemaId` azureMonitorCommonAlertSchema) cannot be reproduced:
several of its fields describe the Azure alert (signal type, target resource ids, the origin alert
id, the alert context) and New Relic has no value for them. The template below fills the fields
that have an honest source and leaves out the rest; a receiver that needs a left-out field breaks.
Values go into JSON through the `json` helper only. What New Relic's engine does with these
helpers is UNVERIFIED (docs/research/newrelic-notifications-and-nrql-reference.md, section 1.2).
"""

SCHEMA_ID = "azureMonitorCommonAlertSchema"
TEMPLATE = "\n".join(
    [
        "{",
        f'  "schemaId": "{SCHEMA_ID}",',
        '  "data": {',
        '    "essentials": {',
        '      "alertId": {{json issueId}},',
        '      "alertRule": {{json issueTitle}},',
        '      "severity": {{#eq priority "CRITICAL"}}"Sev1"{{else}}"Sev3"{{/eq}},',
        '      "monitoringService": "New Relic",',
        '      "monitorCondition": {{#eq state "CLOSED"}}"Resolved"{{else}}"Fired"{{/eq}},',
        '      "firedDateTime": {{json issueCreatedAtUtc}},',
        '      "resolvedDateTime": '
        "{{#if issueClosedAtUtc}}{{json issueClosedAtUtc}}{{else}}null{{/if}},",
        '      "essentialsVersion": "1.0"',
        "    },",
        '    "alertContext": {"issueUrl": {{json issuePageUrl}}}',
        "  }",
        "}",
    ]
)
# (Azure field, New Relic variable or the reason it is missing), for the report
MAPPING: tuple[tuple[str, str], ...] = (
    ("essentials.alertId", "issueId"),
    ("essentials.alertRule", "issueTitle (the converted condition name)"),
    (
        "essentials.severity",
        "priority: CRITICAL gives Sev1, anything else Sev3 (five levels collapse to two)",
    ),
    ("essentials.monitorCondition", "state: CLOSED gives Resolved, otherwise Fired"),
    ("essentials.firedDateTime / resolvedDateTime", "issueCreatedAtUtc / issueClosedAtUtc"),
    ("alertContext", "only the New Relic issue link; the Azure alert context is not available"),
    (
        "essentials.signalType, alertTargetIDs, originAlertId, description, alertContextVersion",
        "left out: no New Relic value",
    ),
    (
        "data.customProperties and per-alert-type payloads of useCommonAlertSchema=false",
        "not reproduced",
    ),
)
