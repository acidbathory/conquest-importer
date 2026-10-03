"""Source platforms. Importing this package registers every source in `base.REGISTRY`."""

from importer.sources import cloudwatch, datadog  # noqa: F401  (import registers the source)
from importer.sources.base import DASHBOARD_REGISTRY, REGISTRY, DashboardSource, Source

__all__ = ["DASHBOARD_REGISTRY", "REGISTRY", "DashboardSource", "Source"]
