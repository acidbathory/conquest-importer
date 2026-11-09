"""The fixed reason sentences of the notification plans.

Reasons are shared by the plans, the taxonomy and the remediation rules, so they live in one
place. They are constants with no text from the export in them: names, matcher values and
template text never reach a reason (they may only be shown through `md_cell`).
"""

# contact points
R_CP_NONE = "grafana contact point has no new relic equivalent"
R_CP_MANUAL = "grafana contact point needs a manual step"
R_CP_WEBHOOK_TEMPLATE = "grafana contact point needs a webhook template"
R_CP_WEBHOOK_SETTINGS = "grafana webhook contact point settings not converted"
R_CP_UNKNOWN = "grafana contact point type is not known"
R_CUSTOM_TEMPLATE = "grafana custom notification template not converted"

# notification policies
R_POLICY_UNDECIDABLE = "grafana notification policy matcher cannot be decided"
R_POLICY_REGEX = "grafana notification policy regular expression cannot be expressed"
R_POLICY_ORDER = "grafana notification policy order cannot be expressed"
R_POLICY_LIMIT = "grafana notification policy tree too large to plan"
R_POLICY_NO_RECEIVER = "grafana notification policy rule has no receiver"
R_POLICY_TIMING = "grafana notification policy grouping or timing not converted"
R_POLICY_BAD_MATCHER = "grafana notification policy matcher not understood"
R_POLICY_INCOMPLETE = "grafana notification policy tree was cut by a size limit"
R_POLICY_MUTE = "grafana notification policy mute timing not converted"

# mute timings
R_MUTE_DAYS = "grafana mute timing days of the month not converted"
R_MUTE_MONTHS = "grafana mute timing months or years not converted"
R_MUTE_LOCATION = "grafana mute timing location not converted"
R_MUTE_TIMES = "grafana mute timing time range not understood"
R_MUTE_WEEKDAYS = "grafana mute timing weekdays not understood"
R_MUTE_ACTIVE = "grafana active time interval not converted"
R_MUTE_COMBINED = "grafana mute timing combines fields that one muting rule cannot express"

# data path
R_DATASOURCE_PATH = "grafana data source has no new relic data path"
R_RULE_GROUP_UNREADABLE = "grafana rule file group could not be read"
R_RECORD_RULE = "grafana rule file recording rule is not an alert"
