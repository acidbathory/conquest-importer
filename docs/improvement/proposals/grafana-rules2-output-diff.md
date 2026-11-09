# Grafana rules, round 2: change in pinned output

The pinned output group `convert/grafana` (fixture folder `tests/fixtures/grafana`) differs from
`tests/fixtures/emitter-output-hashes.json` in two files. The hashes were not regenerated; the owner
approves this diff first. The only expected gate failure is
`test_output_bytes_are_unchanged[convert/grafana]`.

I ran the conversion with the code at the start of this round (0489acb) and with the new code,
and compared the two output folders: only these two files differ.

## ckg-prom-up-zero (`ir/66369877151823.json` and its row in `report.md`)

- The alert: query `up{job="payments"} == 0`, reduced with `last`, then a Grafana threshold node
  "above 0".
- Before: manual, with the reason "compares inside the query and the rule also has a threshold node,
  so the two cannot be one New Relic threshold".
- After: still manual (nothing is converted, no condition is written), with a different reason:
  `up{job="payments"} == 0` keeps only series whose value is 0, and "above 0" is never true for 0,
  so this Grafana rule can never fire.
- Why it is more correct: the old text said the shape was hard to translate. The new text says what
  the rule actually does. In Grafana it never alerts, which is almost certainly a mistake in the
  source rule, so writing a New Relic condition for it would create an alert that behaves
  differently from the original. The manual steps now say that and suggest the usual fix (query
  `up` and fire when it is below 1).
- No file is added or removed, no other IR file, `main.tf` or NerdGraph file changes.

## Behaviour that does not show in the pinned fixtures

- If the threshold node is met by the kept value (for `== 0`: "below 1", "at most 0", "equals 0",
  "at least 0"), the rule now converts with the comparison as the threshold, the same way a
  rule-file rule does. No pinned fixture has this shape; it is covered by tests.
- A Prometheus label named `metricName` (in a filter or a grouping) is now manual, because New Relic
  stores the metric name in an attribute of that name and the filter would test the wrong thing.
- `histogram_quantile` keeps its reason; only the manual steps change (they no longer recommend
  `percentile()`, which is for New Relic distribution metrics, and name `bucketPercentile` as
  unverified for alert conditions).

## Rules not converted, and why

Recording rules (no alert exists), math over two queries and two data sources (a condition reads
one query), range thresholds (two conditions would double the alerts), `unwrap` and parser stages
in LogQL, and `histogram_quantile` (alert-condition support for `bucketPercentile` is not
documented) stay manual. Their reasons were already precise.
