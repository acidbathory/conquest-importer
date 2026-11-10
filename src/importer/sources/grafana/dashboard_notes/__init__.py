"""Manual steps for what the dashboard converter leaves out of a dashboard as a whole.

The converter writes these as dashboard-level reasons (`dashboard links are not carried over`,
`3 tabs were flattened ...`). They are not panels, so the panel rules never matched them. Patterns
are anchored to the converter's wording in `dashboard_convert/reasons.py`; the test
`tests/test_remediation_plan.py` fails when a dashboard-level reason has no rule.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy

ROWS: list[tuple[str, str, list[str]]] = [
    (
        r"^\d+ interval or data source variables were dropped",
        "Dashboard interval or data source variables dropped",
        [
            "A New Relic variable cannot set the time bucket or switch the data source, so these "
            "variables were left out. The bucket is chosen by `TIMESERIES AUTO`; for a fixed "
            "bucket pick one in each query. For a data source variable, decide which New Relic "
            "account holds the data and, for several accounts, use the account selector of the "
            "widget instead.",
        ],
    ),
    (
        r"^annotations are not carried over",
        "Dashboard annotations dropped",
        [
            "New Relic dashboards have no annotation layer. Send the events that Grafana drew "
            "(deployments, incidents) to New Relic as change tracking events so they show as "
            "markers on charts of the entity, or add a widget that lists them.",
        ],
    ),
    (
        r"^dashboard links are not carried over",
        "Dashboard links dropped",
        [
            "New Relic has no link bar. Add a markdown widget at the top of the page with the "
            "links, or set the link of a widget. Links to other Grafana dashboards need the "
            "New Relic dashboard they were converted to.",
        ],
    ),
    (
        r"^\d+ repeated rows are shown once",
        "Dashboard repeated rows shown once",
        [
            "Grafana draws a copy of a repeated row for every value of a variable. The converted "
            "dashboard has one copy. Use a `FACET` on the variable's label inside the widgets "
            "so one widget shows every value, or copy the widgets by hand for the values you "
            "need.",
        ],
    ),
    (
        r"^\d+ tabs were flattened",
        "Dashboard tabs flattened",
        [
            "The tabs became one page with a heading for each. Split the page into New Relic "
            "pages by hand if you want tabs back.",
        ],
    ),
    (
        r"^\d+ widgets over the limit of",
        "Dashboard over the widget limit",
        [
            "A New Relic dashboard page holds at most 150 widgets and the extra ones were left "
            "out. Add a second page to the dashboard and move the remaining widgets there, or "
            "drop the ones nobody reads.",
        ],
    ),
]

register_rules([(re.compile(p), t, s) for p, t, s in ROWS])
for _pattern, _title, _steps in ROWS:
    register_taxonomy(Category.UNSUPPORTED_FEATURE, _pattern)
