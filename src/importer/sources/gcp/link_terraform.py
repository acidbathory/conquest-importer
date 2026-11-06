"""Generate Terraform that links a Google Cloud project to New Relic, for the services used.

Follows New Relic's Workload Identity Federation guide: a service account that can only read, a
workload identity pool whose provider trusts only your New Relic account, then the link and one
polling block per service. No keys are created or stored. Everything is created by `terraform
apply`, which you run yourself: this module only writes text.
"""

import re
from pathlib import Path

from importer.core.emit.terraform import hcl_string
from importer.core.ir import IRMonitor

TEMPLATE = Path(__file__).with_name("link.tf.tmpl")
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
POOL_ID = re.compile(r"^[a-z][a-z0-9-]{2,30}[a-z0-9]$")
# Google metric type prefix -> block name in newrelic_cloud_gcp_dm_integrations
SERVICE_BLOCKS = {
    "aiplatform.googleapis.com": "ai_platform",
    "alloydb.googleapis.com": "alloy_db",
    "apigateway.googleapis.com": "api_gateway",
    "appengine.googleapis.com": "app_engine",
    "bigquery.googleapis.com": "big_query",
    "bigtable.googleapis.com": "big_table",
    "cloudfunctions.googleapis.com": "functions",
    "cloudsql.googleapis.com": "sql",
    "composer.googleapis.com": "composer",
    "compute.googleapis.com": "virtual_machines",
    "dataflow.googleapis.com": "data_flow",
    "dataproc.googleapis.com": "data_proc",
    "datastore.googleapis.com": "data_store",
    "firestore.googleapis.com": "firestore",
    "kubernetes.io": "kubernetes",
    "loadbalancing.googleapis.com": "load_balancing",
    "memcache.googleapis.com": "mem_cache",
    "pubsub.googleapis.com": "pub_sub",
    "redis.googleapis.com": "redis",
    "run.googleapis.com": "run",
    "spanner.googleapis.com": "spanner",
    "storage.googleapis.com": "storage",
    "vpcaccess.googleapis.com": "vpc_access",
}
APIS = [
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudasset.googleapis.com",
    "serviceusage.googleapis.com",
    "monitoring.googleapis.com",
]
ROLES = ["roles/viewer", "roles/serviceusage.serviceUsageConsumer", "roles/cloudasset.viewer"]
ISSUERS = {"us": "https://oidc.newrelic.com/r/gcp-cmp"}


def services_for(monitors: list[IRMonitor]) -> tuple[list[str], list[str]]:
    """Blocks to enable for the converted metric conditions, and prefixes with no known block."""
    blocks: set[str] = set()
    unknown: set[str] = set()
    for m in monitors:
        if m.signal.metric is None:
            continue
        for term in m.signal.metric.terms.values():
            prefix = term.metric.split("/", 1)[0]
            if prefix in SERVICE_BLOCKS:
                blocks.add(SERVICE_BLOCKS[prefix])
            elif prefix:
                unknown.add(prefix)
    return sorted(blocks), sorted(unknown)


def _list(items: list[str], indent: str) -> str:
    return "[\n" + "".join(f"{indent}  {hcl_string(i)},\n" for i in items) + f"{indent}]"


def link_terraform(
    project: str, nr_account_id: int, blocks: list[str], pool_id: str, region: str = "us"
) -> str:
    if not PROJECT_ID.match(project):
        raise ValueError("--project must be a Google Cloud project id such as my-project-123")
    if not POOL_ID.match(pool_id):
        raise ValueError("--pool-id must be 4 to 32 lowercase letters, digits or hyphens")
    if nr_account_id <= 0:
        raise ValueError("--nr-account-id must be your New Relic account id")
    if region not in ISSUERS:
        raise ValueError(
            f"region {region!r} is not supported yet: the OIDC issuer for it is not verified"
        )
    services = "".join(
        f"\n  {b} {{\n    metrics_polling_interval = 300\n  }}\n" for b in sorted(set(blocks))
    )
    template = TEMPLATE.read_text()
    values = {
        "PROJECT": hcl_string(project),
        "NR_ACCOUNT": hcl_string(str(nr_account_id)),
        "POOL": hcl_string(pool_id),
        "APIS": _list(APIS, "  "),
        "ROLES": _list(ROLES, "  "),
        "ISSUER": hcl_string(ISSUERS[region]),
        "SERVICES": services,
    }
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    return template
