# CloudWatch fixtures (synthetic)

`describe-alarms.json` is written by `scripts/generate_cloudwatch_fixtures.py` in the shape of
`aws cloudwatch describe-alarms --output json`. It is **not** exported from a real AWS account.
Replace or extend it with real output when available (check it for sensitive values first).

Covers: classic single-metric alarms, M-of-N datapoints, missing-data modes, metric math,
percentile statistics, anomaly detection, high-resolution periods, a daily period, an unmapped
namespace, a composite alarm, disabled actions, and hostile names/dimension values.
Expected results are in `tests/golden/cloudwatch_expected.json`.
