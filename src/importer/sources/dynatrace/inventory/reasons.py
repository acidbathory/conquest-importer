"""Fixed reason texts for the inventory, re-instrumentation and synthetics plans.

Same rule as dashboards/reasons.py: a reason is a constant sentence with no text from the
input. Each text has a remediation rule (remediation_rules.py) and a taxonomy category
(CATEGORY below, registered in the package `__init__`).
"""

from importer.core.taxonomy import Category

INV = "dynatrace inventory:"
SYN = "dynatrace synthetic:"

I_TYPE = f"{INV} the entity type is not in the technology table"
I_TECH = f"{INV} the technology is not in the technology table"
I_NO_TECH = f"{INV} the service lists no technology"
I_PROCESS = f"{INV} OneAgent process detection is replaced by New Relic agents, not migrated"
I_PARTIAL = f"{INV} the entity list is partial, so the counts are a lower bound"
I_ROW = f"{INV} the target in this row is not confirmed against New Relic documentation"
I_SPELLING = (
    f"{INV} the technology name matched only after changing case, underscores or hyphens, "
    "so the spelling is not confirmed"
)
I_OS = f"{INV} the operating system may not be supported by the New Relic infrastructure agent"

S_API_SCRIPT = f"{SYN} the monitor needs a scripted API monitor that a person writes"
S_BROWSER_SCRIPT = f"{SYN} the monitor needs a scripted browser monitor that a person writes"
S_PRE_POST = (
    f"{SYN} pre-processing and post-processing scripts use Dynatrace's own API and do not port"
)
S_STEP = f"{SYN} the clickpath maps to a step monitor, but the step locator format is not confirmed"
S_NETWORK = f"{SYN} network availability monitors (ICMP, TCP, DNS) have no New Relic monitor"
S_AUTH = (
    f"{SYN} authentication, a client certificate or a regular-expression rule needs a scripted "
    "API monitor that a person writes"
)
S_LOCATION_ROW = (
    f"{SYN} the location was chosen from a mapping row or country fallback that is not confirmed"
)
S_NO_SCRIPT = f"{SYN} the monitor has no script to read"
S_KIND = f"{SYN} the kind of monitor is not known"
S_LOCATION = f"{SYN} a location has no mapping to a New Relic location"
S_PRIVATE = (
    f"{SYN} a private location needs a New Relic private location and a minion installed by hand"
)
S_NO_LOCATION = f"{SYN} the monitor lists no location"
S_FREQUENCY = f"{SYN} the frequency has no exact New Relic period and was rounded up"
S_ALERT = (
    f"{SYN} availability and loading-time thresholds become an alert condition on the "
    "monitor's results, which is not created here"
)
S_REDIRECT = (
    f"{SYN} the redirect setting maps to treat_redirect_as_failure and the direction is not "
    "confirmed"
)
S_USER_AGENT = f"{SYN} the user agent has no field on a simple monitor and was dropped"
S_DISABLED = f"{SYN} the monitor is disabled in Dynatrace"

CATEGORY: dict[str, Category] = {
    I_TYPE: Category.MISSING_MAPPING,
    I_TECH: Category.MISSING_MAPPING,
    I_NO_TECH: Category.MISSING_MAPPING,
    I_PROCESS: Category.UNSUPPORTED_FEATURE,
    I_PARTIAL: Category.MANUAL_FOLLOWUP,
    I_ROW: Category.UNVERIFIED_NAMES,
    I_OS: Category.UNVERIFIED_NAMES,
    I_SPELLING: Category.UNVERIFIED_NAMES,
    S_API_SCRIPT: Category.UNSUPPORTED_FEATURE,
    S_BROWSER_SCRIPT: Category.UNSUPPORTED_FEATURE,
    S_PRE_POST: Category.UNSUPPORTED_FEATURE,
    S_STEP: Category.UNVERIFIED_NAMES,
    S_NETWORK: Category.UNSUPPORTED_FEATURE,
    S_AUTH: Category.UNSUPPORTED_FEATURE,
    S_LOCATION_ROW: Category.UNVERIFIED_NAMES,
    S_NO_SCRIPT: Category.UNSUPPORTED_FEATURE,
    S_KIND: Category.UNSUPPORTED_FEATURE,
    S_LOCATION: Category.MISSING_MAPPING,
    S_PRIVATE: Category.MANUAL_FOLLOWUP,
    S_NO_LOCATION: Category.MANUAL_FOLLOWUP,
    S_FREQUENCY: Category.APPROXIMATION,
    S_ALERT: Category.MANUAL_FOLLOWUP,
    S_REDIRECT: Category.UNVERIFIED_NAMES,
    S_USER_AGENT: Category.APPROXIMATION,
    S_DISABLED: Category.MANUAL_FOLLOWUP,
}
ALL = tuple(CATEGORY)
