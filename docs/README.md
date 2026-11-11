# Documentation

**Using the importer**
- Runbooks, one per source product:
  [AWS CloudWatch](user/runbook-aws-cloudwatch.md), [Google Cloud](user/runbook-gcp.md),
  [Grafana](user/runbook-grafana.md), [Dynatrace](user/runbook-dynatrace.md),
  [Azure Monitor](user/runbook-azure.md), [Datadog](user/runbook-datadog.md)
- [Azure credentials](user/azure-credentials.md): a read-only service principal, step by step
- [Script and command reference](user/script-reference.md): every script and command, by source
- [The guided menu and rollback](user/menu-and-rollback.md)
- [Remediation cookbook](user/remediation-cookbook.md): ranked ways to finish what New Relic cannot do one-to-one
- [Getting started (AWS walkthrough)](getting-started.md): AWS CloudWatch, install to rollback, step by step
- [What migrates](what-migrates.md): everything that does and does not move, and why
- [Permissions](permissions.md): per source (AWS, Google Cloud, Grafana, Dynatrace) and the New Relic keys
- [Troubleshooting](troubleshooting.md): error messages and fixes
- [Sharing results safely](customer-export.md)

**Contributing and design**
- [Linting and checks](dev/linting.md), [DRY guidelines](dev/dry-guidelines.md), [Testing](dev/testing.md)
- [Intermediate format](ir-spec.md), [Threat model](threat-model.md)
