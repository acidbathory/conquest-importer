## 2026-11-11 gcp placeholders and filters

- The two blockers were not conversion gaps. Every `${NAME}` in the 18 PromQL conditions is a
  sample-policy variable that nothing in the policy defines, and the "filter not supported" group
  mixed six unrelated causes. Nothing in them can be converted without guessing, so the converted
  count stays at 170 and the fix is in the reasons and the playbook.
- A remediation rule that matches a loose phrase ("log filter") also catches reasons from other
  causes (log-based metrics) and hides them in the wrong group. Do not anchor it: reasons get prefixes
  ("ratio denominator: ..."), so put the specific rules before the generic one instead.
- Names copied from a query into a reason are restricted to letters, digits and underscore; the
  shared placeholder pattern accepts any Unicode word character.
- The name map already resolves PromQL placeholders (`variables`, env `IMPORTER_GRAFANA_NAME_MAP`).
  The reason now lists the names that have no value and the playbook says how to give them one.
- Regular expressions with real syntax need RLIKE, which the count and tag filters in the IR
  cannot hold (frozen file). Requested in the proposal, not worked around.
