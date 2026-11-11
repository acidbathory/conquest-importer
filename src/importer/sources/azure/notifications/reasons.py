"""Reasons the Azure notification conversion produces: fixed sentences, no text from the export.

Names, addresses, URLs, phone numbers and secret names never appear in a reason. Every reason
starts with "azure " so the anchored remediation and taxonomy patterns of this package claim these
and no other source's reasons.
"""

# action group receivers
R_SECRET = "azure converted webhook destination needs a secret supplied at apply time"  # noqa: S105 - a sentence
R_COMMON_SCHEMA = "azure webhook receiver gets a payload that is not the common alert schema"
R_AAD = "azure webhook receiver authenticates with Microsoft Entra, which a script cannot set up"
R_NO_URL = "azure receiver has no usable callback url in the export"
R_URL_CREDENTIALS = "azure webhook url carries a user name and password"
R_HTTP_URL = "azure webhook url is not https"
R_NO_EMAIL = "azure email receiver has no usable address"
R_EMAIL_CUT = "azure email receivers beyond the 150 address limit were left out"
R_SMS = "azure sms receiver has no new relic destination"
R_VOICE = "azure voice receiver has no new relic destination"
R_APP_PUSH = "azure app push receiver can only be set up by each person"
R_EVENT_HUB = "azure event hub receiver has no new relic destination"
R_ARM_ROLE = "azure arm role receiver has no new relic destination"
R_ITSM = "azure itsm receiver needs a servicenow destination made by a person"
R_UNKNOWN_RECEIVER = "azure action group has a receiver type this importer does not know"
R_GROUP_DISABLED = "azure action group is disabled"
R_GROUP_NOT_EXPORTED = "azure alert rule points at an action group that is not in the export"
R_TAG_FILTER = "azure workflow filter depends on the tag written by the alert conversion"

# alert processing rules
R_RULE_ADD = "azure alert processing rule adds action groups"
R_RULE_LEGACY = "azure action rule uses the 2019 preview api"
R_RULE_ACTION = "azure alert processing rule has an action type this importer does not know"
R_RULE_FIELD = "azure alert processing rule condition has nothing in new relic to match on"
R_RULE_OPERATOR = "azure alert processing rule condition cannot be said as a muting condition"
R_RULE_TAG = "azure alert processing rule condition needs a tag the converted conditions lack"
R_RULE_SCOPE = "azure alert processing rule scope cannot be matched to converted conditions"
R_RULE_SCOPE_RULE_SUB = (
    "azure alert processing rule scope was matched by the alert rule subscription"
)
R_RULE_NO_MATCH = "azure alert processing rule matches no converted condition"
R_RULE_SCHEDULE = "azure alert processing rule schedule cannot be a muting window"
R_RULE_ZONE = "azure alert processing rule time zone is not recognised"
R_RULE_SEVERAL = "azure alert processing rule became several muting rules"
R_RULE_ALWAYS = "azure alert processing rule has no schedule so its muting rule is always active"
R_RULE_EXPIRED = "azure alert processing rule schedule ended before today"
R_RULE_TOO_MANY = "azure alert processing rule matches more conditions than a muting rule holds"
