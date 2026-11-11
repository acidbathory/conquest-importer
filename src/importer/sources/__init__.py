"""Source platforms. Importing this package registers every source in `base.REGISTRY`."""

from importer.sources import (  # noqa: F401  (import registers the source)
    azure,
    cloudwatch,
    datadog,
    dynatrace,
    gcp,
    grafana,
)
from importer.sources.base import DASHBOARD_REGISTRY, REGISTRY, DashboardSource, Source

__all__ = ["DASHBOARD_REGISTRY", "REGISTRY", "DashboardSource", "Source"]
