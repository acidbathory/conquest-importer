"""Azure action groups and alert processing rules: reading the exports without trusting them."""

import json
from pathlib import Path

import pytest

from importer.sources.azure.notifications.link import (
    ACTION_GROUP_TAG,
    action_group_key,
    monitor_action_groups,
)
from importer.sources.azure.notifications.models import load_export

FIX = Path(__file__).parent / "fixtures" / "azure"


def test_the_tag_name_is_the_agreed_constant() -> None:
    assert ACTION_GROUP_TAG == "azure.actionGroup"


def test_action_group_key_ignores_case_and_keeps_subscription_group_and_name() -> None:
    a = "/subscriptions/S1/resourceGroups/RG/providers/microsoft.insights/actionGroups/Ops"
    b = "/subscriptions/s1/resourcegroups/rg/providers/Microsoft.Insights/actiongroups/OPS"
    assert action_group_key(a) == action_group_key(b) == "s1/rg/ops"


def test_the_list_response_and_the_wrapped_example_load_the_same_group() -> None:
    export = load_export(FIX / "action_groups" / "2023-01-01__listActionGroups.json")
    group = next(g for g in export.action_groups if g.name == "SampleActionGroup")
    assert group.short_name == "sample" and group.enabled
    assert [r.kind for r in group.receivers].count("emailReceivers") == 2


def test_a_folder_is_deduplicated_by_resource_id() -> None:
    export = load_export(FIX / "action_groups")
    keys = [action_group_key(g.id) for g in export.action_groups if g.id]
    assert len(keys) == len(set(keys))


def test_unknown_receiver_kinds_and_nulls_are_reported_not_fatal() -> None:
    export = load_export(FIX / "synthetic" / "SYNTHETIC__action_group_unknown_receiver_types.json")
    group = export.action_groups[0]
    assert sorted(group.unknown_kinds) == [
        "futureReceivers",
        "pagerDutyReceivers",
        "slackReceivers",
    ]
    assert {r.kind for r in group.receivers} == {"smsReceivers", "webhookReceivers"}


def test_processing_rules_load_from_the_wrapped_request() -> None:
    export = load_export(FIX / "alert_processing_rules")
    assert "DailySuppression" in {r.name for r in export.rules}
    assert any(not r.legacy and r.actions for r in export.rules)


def test_the_retired_2019_api_is_marked_and_loses_to_the_current_api_for_one_rule() -> None:
    old = load_export(
        FIX
        / "alert_processing_rules"
        / "2019-05-05-preview__ActionRules_Create_SuppressionRule.json"
    )
    assert [r.legacy for r in old.rules] == [True]
    both = load_export(FIX / "alert_processing_rules")
    daily = [r for r in both.rules if r.name == "DailySuppression"]
    assert len(daily) == 1 and not daily[0].legacy


def test_a_broken_resource_becomes_a_problem_without_its_text(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text(
        json.dumps(
            {
                "type": "Microsoft.Insights/actionGroups",
                "name": "n",
                "properties": {"emailReceivers": [{"name": ["bad"], "emailAddress": "SECRETTEXT"}]},
            }
        )
    )
    export = load_export(tmp_path)
    assert export.problems and "SECRETTEXT" not in " ".join(export.problems)


def test_a_missing_path_is_an_error() -> None:
    with pytest.raises(ValueError, match="no JSON"):
        load_export(FIX / "nope")


def test_monitor_references_come_from_handles_and_tags() -> None:
    from importer.core.ir import IRMonitor

    m = IRMonitor.model_validate(
        {
            "source_system": "azure",
            "source_id": 1,
            "source_type": "t",
            "name": "n",
            "tags": [f"{ACTION_GROUP_TAG}:S1/RG/Ops", "other:x"],
            "signal": {"kind": "unknown", "raw_query": ""},
            "confidence": "exact",
            "notification": {
                "handles": [
                    "/subscriptions/S1/resourceGroups/RG/providers/microsoft.insights/actionGroups/Pager"
                ]
            },
        }
    )
    assert monitor_action_groups(m) == ["s1/rg/ops", "s1/rg/pager"]


def test_the_azure_pair_header_comes_from_the_shared_brand_table() -> None:
    from importer.core.report_brand import header_lines, pair_name

    assert pair_name("azure") == "Azure Monitor to New Relic"
    assert header_lines("Azure notification conversion", "azure")[0] == (
        "# Azure notification conversion: Azure Monitor to New Relic"
    )


def test_alert_rules_in_the_same_folder_are_not_read_as_processing_rules() -> None:
    export = load_export(FIX)
    names = {r.name for r in export.rules}
    assert "SampleActivityLogAlertRule" not in names and "highcpu" not in names
    assert len(export.rules) <= 12  # the processing rule and action rule examples only
