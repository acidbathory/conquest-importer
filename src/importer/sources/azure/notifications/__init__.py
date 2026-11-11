"""Azure notifications: action groups and alert processing rules to New Relic.

Importing the package registers its remediation rules, taxonomy patterns and playbooks. The
alert converter can import `ACTION_GROUP_TAG` and `action_group_key` from `link`.
"""

from importer.sources.azure.notifications import (  # noqa: F401  (import registers the rules)
    playbooks,
    remediation_rules,
)
from importer.sources.azure.notifications.link import ACTION_GROUP_TAG, action_group_key

__all__ = ["ACTION_GROUP_TAG", "action_group_key"]
