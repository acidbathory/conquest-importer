"""Fill `${NAME}` placeholders of a PromQL query from the name map's defaults.

Sample policies and rule templates leave `${CLUSTER_NAME}`-style placeholders for a person to
replace. A default in the name map (`variables`) stands for that person. Every use is noted, and
a placeholder without a default is reported so the caller refuses the query as before: a
placeholder is never dropped or guessed.
"""

import re

from importer.sources.grafana.dashboard_convert.name_map import NameMapError, active_name_map

PLACEHOLDER = re.compile(r"\$\{(\w+)\}")
N_FILLED = (
    "the placeholder ${{{name}}} was replaced by the default {value!r} from the name map "
    "(variables); check that it is the value you mean"
)


def fill_placeholders(query: str) -> tuple[str, list[str], list[str]]:
    """(query, notes, names without a default). The query is unchanged when any name is missing."""
    names = list(dict.fromkeys(PLACEHOLDER.findall(query)))
    if not names:
        return query, [], []
    try:
        defaults = active_name_map().variables
    except NameMapError:
        defaults = {}
    missing = [name for name in names if name not in defaults]
    if missing:
        return query, [], missing
    filled = PLACEHOLDER.sub(lambda m: defaults[m[1]], query)
    return filled, [N_FILLED.format(name=n, value=defaults[n]) for n in names], []
