"""Dynatrace inventory: extractor, re-instrumentation plan and synthetic monitor plan.

Importing this package registers the remediation rules and the taxonomy phrases for the
reasons in reasons.py. Each phrase is the whole reason text anchored at the start, so it cannot
capture the wording of another source.
"""

import re

from importer.core.taxonomy import register_taxonomy
from importer.sources.dynatrace.inventory import (
    reasons,
    remediation_rules,  # noqa: F401  (registers)
)

for _reason, _category in reasons.CATEGORY.items():
    register_taxonomy(_category, "^" + re.escape(_reason.lower()))
