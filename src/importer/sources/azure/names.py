"""New Relic names for Azure metrics, dimensions and log columns.

The Azure Monitor integration names a metric from the resource type and the metric name
(docs/research/azure-datapath.md section 2, from the New Relic documentation): the type is
lower-cased, `/` becomes `.`, `Microsoft.` becomes `azure.`, and the metric name keeps its case
with white space removed. `Microsoft.Compute/virtualMachines` + `Percentage CPU` is
`azure.compute.virtualmachines.PercentageCPU`.

Nothing here has been seen arriving in a New Relic account, so a derived name is always reported
as UNVERIFIED. A name from `name-map.json` replaces it and is reported as coming from the map.
"""

import re
from dataclasses import dataclass

from importer.sources.azure.models import NameMap

PLATFORM_TYPE = re.compile(r"microsoft\.[a-z0-9]+(?:/[a-z0-9]+)+")
_METRIC = re.compile(r"[A-Za-z0-9_]+")
_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,100}")
_DIMENSION = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,100}")
RESOURCE_SEGMENTS = re.compile(r"/providers/([^/]+)/([^/]+)/[^/]+((?:/[^/]+/[^/]+)*)/?$", re.I)


@dataclass(frozen=True)
class MetricName:
    attribute: str
    prefix: str  # the resource type prefix, which dimension attributes also start with
    from_map: bool


def type_prefix(resource_type: str) -> str | None:
    """`azure.compute.virtualmachines` for `Microsoft.Compute/virtualMachines`, else None."""
    lowered = resource_type.strip().lower()
    if not PLATFORM_TYPE.fullmatch(lowered):
        return None
    return "azure." + lowered.removeprefix("microsoft.").replace("/", ".")


def type_of_resource_id(resource_id: str) -> str | None:
    """`Microsoft.Compute/virtualMachines` from a full resource id, else None."""
    match = RESOURCE_SEGMENTS.search(resource_id)
    if match is None:
        return None
    subtypes = re.findall(r"/([^/]+)/[^/]+", match[3])
    return "/".join([match[1], match[2], *subtypes])


def _lookup(table: dict[str, str], *keys: str) -> str | None:
    lowered = {k.lower(): v for k, v in table.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def metric_name(names: NameMap, namespace: str, metric: str) -> MetricName | None:
    """The New Relic metric for an Azure metric: the name map first, then the documented rule."""
    mapped = _lookup(names.metrics, f"{namespace}/{metric}", metric)
    prefix = type_prefix(namespace)
    if mapped is not None:
        return MetricName(mapped, prefix or mapped.rsplit(".", 1)[0], True)
    squeezed = re.sub(r"\s+", "", metric)
    if prefix is None or not _METRIC.fullmatch(squeezed):
        return None
    return MetricName(f"{prefix}.{squeezed}", prefix, False)


def dimension_attribute(names: NameMap, prefix: str, dimension: str) -> str | None:
    """The attribute a metric dimension arrives as (`azure.cache.redis.ShardId`), or None."""
    mapped = _lookup(names.attributes, dimension)
    if mapped is not None:
        return mapped
    squeezed = re.sub(r"\s+", "", dimension)
    return f"{prefix}.{squeezed}" if _DIMENSION.fullmatch(squeezed) else None


def log_attribute(names: NameMap, table: str, column: str) -> tuple[str, bool] | None:
    """(attribute, from the map) for a log column; the column name is used as it is otherwise."""
    mapped = _lookup(names.attributes, f"{table}.{column}", column)
    if mapped is not None:
        return mapped, True
    return (column, False) if _COLUMN.fullmatch(column) else None
