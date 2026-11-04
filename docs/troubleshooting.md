# Troubleshooting

Start with `importer doctor`. It checks Python, your AWS keys and your New Relic key.

| You see | Why | Fix |
|---|---|---|
| `no AWS credentials found` | No keys in the environment and no profile | Export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (and `AWS_SESSION_TOKEN` if temporary), or pass `--profile` |
| `ExpiredToken` / `InvalidClientTokenId` | Temporary keys ran out, or the key is wrong | Get fresh keys |
| `AccessDenied` on `DescribeAlarms`, `ListDashboards`… | The keys lack a permission | Add the actions in [permissions](permissions.md) |
| `the AWS credentials belong to account X, not Y; nothing was read` | Your keys are for a different account than `--aws-account-id` | Use the right keys, or correct the id. The check is deliberate |
| `that AWS profile does not exist` | Typo in `--profile` | Check `~/.aws/credentials` and `~/.aws/config` |
| `invalid AWS region` | Not a region name | Use e.g. `us-east-1` |
| `Read account … 0 metric alarms` then `No monitors or alarms found` | That region has no alarms | Try another `--region`; alarms are per region |
| `… already contains JSON files; use a new --out folder` | A folder from an earlier run | Choose a new `--out`. The tool never mixes runs |
| `NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set` | The write safety catch | `export NEW_RELIC_ALLOWED_ACCOUNT_IDS=<account id>` |
| `account … is not in NEW_RELIC_ALLOWED_ACCOUNT_IDS` | The account isn't on the allow-list | Add it to the variable |
| `--confirm-account must repeat the account id to apply` | The confirmation is missing or differs | Pass `--confirm-account` with the same account id |
| `Environment variable NEW_RELIC_API_KEY is not set`, or a New Relic API error | No key, wrong key, or EU account | Use a User key (`NRAK-…`); add `--nr-region eu` for EU accounts |
| Converted condition shows no data in New Relic | Metrics aren't streaming, or the metric name differs | Deploy the ingest Terraform ([step 6](getting-started.md#step-6-stream-the-metrics)), then `importer validate` |
| Values in New Relic are double those in AWS | Polling integration and Metric Streams both run | Turn one off |
| TLS / certificate errors on a company network | The network re-signs HTTPS | The tool uses your operating system's trust store, so make sure your company certificate is installed there. Don't turn verification off |

If you're still stuck, run the command again with the same arguments and open a
[bug report](../../../issues/new/choose) with the exact message. Remove account ids first.
