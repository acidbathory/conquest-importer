# Pending owner review: CloudWatch golden file

`cloudwatch_expected.proposed.json` is what `tests/golden/cloudwatch_expected.json` would contain
with the 2026-10-05 converter fixes. The real golden file is NOT changed yet: project rules say a
human reviews the diff before it is regenerated. Until then `test_cloudwatch_fixtures_match_golden`
fails (everything else passes).

What changes (23 entries, same keys, no shape changes):
- 13 of the 17 converted queries gain `... IS NULL` pins for the dimensions the alarm does not use.
  Why: CloudWatch publishes a metric once per dimension set and New Relic stores all sets under one
  metric name, so an unpinned query adds the sets together (seen live: API Gateway count 108 vs 54).
- `alb-latency-p99` goes from manual to approximate:
  `SELECT max(aws.applicationelb.TargetResponseTime.p99) ...` (percentiles are now converted).

To review: `diff <(python3 -m json.tool tests/golden/cloudwatch_expected.json) <(python3 -m json.tool docs/pending/cloudwatch_expected.proposed.json)`
To approve: `UPDATE_GOLDEN=1 uv run pytest tests/test_cloudwatch.py -k golden`, check that
`git diff tests/golden` shows only the changes above, then commit and delete `docs/pending/`.
