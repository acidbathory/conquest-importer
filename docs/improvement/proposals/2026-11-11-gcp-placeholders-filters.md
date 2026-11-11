# GCP placeholders and filters (2026-11-11)

Estate measure (`convert --source gcp` on `tests/fixtures/gcp-estate/policies/*/*.json` flattened,
then `plan-remediation`): 350 conditions, 170 convert, 127 manual (197 emitted before and after).

## Finding

No manual case in the two groups can be converted exactly.

- PromQL template placeholder (18): every name (`${WORKLOAD_NAME}`, `${AGENT_URN}`, `${QUERY}`...)
  is a sample-policy variable; the policy holds no value for it. Resolving is already possible
  through the name map `variables`; nothing is guessed.
- Filter not supported (reported 17 of 19) was six causes: system labels `metadata.system_labels.*`
  (6, attribute unconfirmed), SLO selectors (4), log-based metrics (matched the rule by the words
  "log filter"), a regex that is a placeholder (3), an OR of patterns (1), a regex
  needing RLIKE (1).

## Change

Reasons, remediation titles and playbook only. The NerdGraph and Terraform output is byte-identical to
before; the remediation titles of several filter reasons changed by design (for example an OR
between labels or over a value list now reads "Filter with an OR", a system label "System label
filter"). A metric-type OR (`an OR of several metric types`) keeps the generic title.

- `promql.py`: the reason lists the placeholder names with no value; the playbook explains the name
  map `variables`.
- `promql.py` also keeps only plain names (letters, digits, underscore) in the reason.
- `scope.py`: a system label says why it is manual.
- `remediation_rules.py`: new titles System label filter, SLO selector, Filter value is a template
  placeholder, Regular expression filter, Filter with an OR; the log-based metric rule now comes
  before the generic filter rule so those reasons reach their own group (the generic rule stays
  unanchored, so prefixed reasons such as "ratio denominator: ..." still match). Groups 18 -> 23, full playbooks 63 -> 67.

## Pinned output

No pinned GCP output changed (the gate diff of `tests/golden/` and the pins is clean).

## Request for the integrator (frozen file)

`core/ir.py` `CountFilter` and `TagFilter` have no regular-expression match, so a Cloud Logging `=~`
with real syntax (`timeout after [0-9]+ms`) cannot become `RLIKE`. Proposed: a `regex: bool` flag
that emits `attr RLIKE '<pattern>'` through `quote_string`, with `.*(...).*` added for a search
regex. Not done here.
