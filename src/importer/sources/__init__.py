"""Source platforms. Importing this package registers every source in `base.REGISTRY`."""

from importer.sources import cloudwatch, datadog  # noqa: F401  (import registers the source)
from importer.sources.base import REGISTRY, Source

__all__ = ["REGISTRY", "Source"]
