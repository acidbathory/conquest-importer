"""Sourced Azure metric names (name_table.json) and how names.py uses them."""

import json
from pathlib import Path
from typing import Any

import pytest

from azure_helpers import SUB, criterion, metric_alert
from importer.core.emit.nrql import build_nrql
from importer.core.ir import Confidence
from importer.core.taxonomy import Category, classify
from importer.sources.azure import name_table
from importer.sources.azure.models import NameMap
from importer.sources.azure.names import dimension_attribute, metric_name
from importer.sources.azure.translate import translate_alert

NONE = NameMap()


def _convert(**crit: Any) -> Any:
    (monitor,) = translate_alert(metric_alert(criterion(**crit)))
    return monitor


# -- storage, the live estate's metrics ----------------------------------------------------------
@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("Transactions", "azure.storage.storageaccounts.Transactions"),
        ("Availability", "azure.storage.storageaccounts.Availability"),
        ("SuccessServerLatency", "azure.storage.storageaccounts.SuccessServerLatency"),
        ("SuccessE2ELatency", "azure.storage.storageaccounts.SuccessE2ELatency"),
        ("UsedCapacity", "azure.storage.storageaccounts.UsedCapacity"),
        ("Ingress", "azure.storage.storageaccounts.Ingress"),
        ("Egress", "azure.storage.storageaccounts.Egress"),
    ],
)
def test_storage_account_metrics_are_sourced(metric: str, expected: str) -> None:
    found = metric_name(NONE, "Microsoft.Storage/storageAccounts", metric)
    assert found is not None
    assert found.attribute == expected
    assert found.basis == "table"
    assert found.scale == 1.0


def test_storage_sub_service_namespaces_have_their_own_prefix() -> None:
    found = metric_name(NONE, "Microsoft.Storage/storageAccounts/blobServices", "BlobCapacity")
    assert found is not None
    assert found.attribute == "azure.storage.storageaccounts.blobservices.BlobCapacity"
    assert found.basis == "table"


# -- compute -------------------------------------------------------------------------------------
def test_vm_metric_with_a_space_and_the_namespace_in_any_case() -> None:
    found = metric_name(NONE, "microsoft.compute/virtualmachines", "Percentage CPU")
    assert found is not None
    assert found.attribute == "azure.compute.virtualmachines.PercentageCPU"
    assert found.basis == "table"


def test_vm_metric_with_a_slash_uses_the_documented_dot() -> None:
    found = metric_name(NONE, "Microsoft.Compute/virtualMachines", "Disk Read Operations/Sec")
    assert found is not None
    assert found.attribute == "azure.compute.virtualmachines.DiskReadOperations.Sec"
    assert found.basis == "table"


def test_scale_set_slash_names_follow_their_own_documented_spelling() -> None:
    found = metric_name(
        NONE, "Microsoft.Compute/virtualMachineScaleSets", "Data Disk Read Bytes/sec"
    )
    assert found is not None
    assert found.attribute == "azure.compute.virtualmachinescalesets.DataDiskReadBytessec"


# -- the other services the estate and fixtures use ----------------------------------------------
@pytest.mark.parametrize(
    ("namespace", "metric", "expected"),
    [
        (
            "Microsoft.Sql/servers/databases",
            "cpu_percent",
            "azure.sql.servers.databases.cpu_percent",
        ),
        ("Microsoft.Web/serverfarms", "CpuPercentage", "azure.web.serverfarms.CpuPercentage"),
        ("Microsoft.KeyVault/vaults", "Availability", "azure.keyvault.vaults.Availability"),
        (
            "Microsoft.EventHub/Namespaces",
            "IncomingMessages",
            "azure.eventhub.namespaces.IncomingMessages",
        ),
        (
            "Microsoft.Network/loadBalancers",
            "VipAvailability",
            "azure.network.loadbalancers.VipAvailability",
        ),
        (
            "Microsoft.Network/applicationgateways",
            "UnhealthyHostCount",
            "azure.network.applicationgateways.UnhealthyHostCount",
        ),
    ],
)
def test_other_services_are_sourced(namespace: str, metric: str, expected: str) -> None:
    found = metric_name(NONE, namespace, metric)
    assert found is not None
    assert found.attribute == expected
    assert found.basis == "table"


def test_a_documented_name_for_a_type_without_a_learn_row_is_still_sourced() -> None:
    name = "azure.apimanagement.service.Capacity"
    assert name_table.is_documented(name)
    found = metric_name(NONE, "Microsoft.ApiManagement/service", "Capacity")
    assert found is not None and found.attribute == name
    assert found.basis == "table"


# -- what stays derived --------------------------------------------------------------------------
def test_an_unlisted_metric_keeps_the_derived_name_and_is_not_sourced() -> None:
    found = metric_name(NONE, "Microsoft.Storage/storageAccounts", "NoSuchMetricAnywhere")
    assert found is not None
    assert found.attribute == "azure.storage.storageaccounts.NoSuchMetricAnywhere"
    assert found.basis == "derived"


def test_aks_cluster_metrics_are_not_in_the_new_relic_table_and_stay_derived() -> None:
    found = metric_name(
        NONE, "Microsoft.ContainerService/managedClusters", "node_cpu_usage_percentage"
    )
    assert found is not None and found.basis == "derived"


def test_an_unlisted_name_with_a_slash_is_still_refused() -> None:
    assert metric_name(NONE, "Microsoft.Compute/virtualMachines", "Made Up/Sec") is None


def test_the_name_map_wins_over_the_table() -> None:
    names = NameMap(metrics={"Transactions": "azure.mine.tx"})
    found = metric_name(names, "Microsoft.Storage/storageAccounts", "Transactions")
    assert found is not None
    assert (found.attribute, found.basis) == ("azure.mine.tx", "map")


# -- dimensions ----------------------------------------------------------------------------------
def test_a_dimension_named_like_its_metric_is_uncapitalised() -> None:
    prefix = "azure.network.applicationgateways"
    got = dimension_attribute(NONE, prefix, "TlsProtocol", metric="TlsProtocol")
    assert got == "azure.network.applicationgateways.tlsprotocol"
    assert dimension_attribute(NONE, prefix, "BackendSettingsPool", metric="TlsProtocol") == (
        "azure.network.applicationgateways.BackendSettingsPool"
    )
    assert dimension_attribute(NONE, prefix, "ApiName") == f"{prefix}.ApiName"


# -- the loader ----------------------------------------------------------------------------------
def test_row_carries_unit_aggregations_and_dimensions_from_microsoft_learn() -> None:
    row = name_table.lookup("microsoft.storage/storageaccounts", "Availability")
    assert row is not None
    assert row.unit == "Percent"
    assert "Average" in row.aggregations
    assert "ApiName" in row.dimensions
    assert row.scale == 1.0


def test_otel_metric_name_follows_the_receiver_code() -> None:
    assert (
        name_table.otel_metric_name("Percentage CPU", "Average") == "azure_percentage_cpu_average"
    )
    assert name_table.otel_metric_name("Transactions", "Total") == "azure_transactions_total"
    assert name_table.otel_metric_name("Data Disk Read Bytes/sec", "Maximum") == (
        "azure_data_disk_read_bytes/sec_maximum"
    )


def test_data_file_is_consistent() -> None:
    data = json.loads(Path(name_table.__file__).with_name("name_table.json").read_text())
    documented = set(data["documented_names"])
    assert len(data["metrics"]) > 300
    for key, row in data["metrics"].items():
        assert key == f"{row['type'].lower()}|{row['metric'].lower()}"
        assert row["name"] in documented
        assert row["name"].startswith("azure.")
        assert row["unit"]
    assert data["default_scale"] == 1.0
    assert {"activity_columns", "resource_log_columns", "agent_tables"} <= set(data["logs"])


# -- the conversion ------------------------------------------------------------------------------
def test_a_converted_rule_says_the_name_is_sourced_not_unverified() -> None:
    m = _convert(metricName="Percentage CPU")
    assert m.confidence is Confidence.APPROXIMATE
    note = next(r for r in m.reasons if r.startswith("metric name"))
    assert "azure.compute.virtualmachines.PercentageCPU" in note
    assert "UNVERIFIED" not in note
    assert "VERIFIED" in note
    assert classify(note) is Category.UNVERIFIED_NAMES
    assert "average(azure.compute.virtualmachines.PercentageCPU)" in build_nrql(m)


def test_a_converted_rule_on_an_unlisted_metric_still_says_unverified() -> None:
    m = _convert(metricName="Nothing Listed")
    note = next(r for r in m.reasons if r.startswith("metric name"))
    assert "UNVERIFIED" in note


def test_a_slash_metric_now_converts() -> None:
    m = _convert(metricName="Disk Read Operations/Sec", timeAggregation="Average")
    assert m.confidence is Confidence.APPROXIMATE
    assert "azure.compute.virtualmachines.DiskReadOperations.Sec" in build_nrql(m)


def test_storage_alert_filters_on_the_resource_id_of_the_scope() -> None:
    scope = f"{SUB}/resourceGroups/rg1/providers/Microsoft.Storage/storageAccounts/sa1"
    (m,) = translate_alert(
        metric_alert(
            criterion(
                metricName="Transactions",
                metricNamespace="Microsoft.Storage/storageAccounts",
                timeAggregation="Total",
            ),
            scopes=[scope],
        )
    )
    nrql = build_nrql(m)
    assert "sum(azure.storage.storageaccounts.Transactions)" in nrql
    assert f"azure.resourceId = '{scope}'" in nrql


@pytest.mark.parametrize(
    ("namespace", "metric", "derived"),
    [
        (
            "Microsoft.ServiceBus/Namespaces",
            "ActiveMessages",
            "azure.servicebus.namespaces.ActiveMessages",
        ),
        (
            "Microsoft.DocumentDB/DatabaseAccounts",
            "TotalRequests",
            "azure.documentdb.databaseaccounts.TotalRequests",
        ),
    ],
)
def test_names_the_documentation_only_prints_with_a_by_suffix_stay_derived(
    namespace: str, metric: str, derived: str
) -> None:
    # The table lists `<name>.byNamespace`, `.byQueue`, `.byAccount` ... but never the plain name.
    found = metric_name(NONE, namespace, metric)
    assert found is not None
    assert (found.attribute, found.basis) == (derived, "derived")
    assert name_table.is_documented(
        derived + (".byNamespace" if "servicebus" in derived else ".byAccount")
    )
