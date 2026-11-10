"""Playbook content, one module per area; importing this package registers all of it."""

from importer.core.playbooks import (  # noqa: F401
    grafana_dashboards,
)
