# Google Cloud: what the combined branches change in the pinned output

Nothing was approved or regenerated. The pinned hash files are exactly as the owner left them, so a
few tests fail on purpose until you decide. This page says, in plain words, what each changed file
is and why. The old output is the one pinned today; the new output is what the combined code
(alert fields, dashboards and channels, data path, filters, PromQL) writes.

To approve everything below in one step, read this page and run:

    uv run python scripts/improve/regenerate_gcp_pins.py          # preview, writes nothing
    uv run python scripts/improve/regenerate_gcp_pins.py --apply  # write the new pins

It rewrites only the Google parts of four fixture files, deletes the two "expected to fail" markers,
and stops without writing if any non-Google output would change. It never touches `tests/golden`.

## The fixtures involved

- **Alert policies** (`tests/fixtures/gcp`): 4 policies read, 3 converted. One is a CPU-high
  condition (policy 5497...), one is a log-match condition (policy 2076...).
- **Dashboards** (`tests/fixtures/gcp-dashboards`): one demo dashboard "Ops overview" with 10 widgets.
- **Notification channels** (`tests/fixtures/gcp-notifications`): 4 channels (email, PagerDuty,
  Slack, webhook) used by two routing sets.

## Alert output (pinned group `convert/gcp`, 6 files, and the same files inside the saved IR check)

| Pinned file | What it is | Old output said | New output says | Why the new one is more correct |
|---|---|---|---|---|
| `ir/253922000910239.json`, `nerdgraph/253922000910239.json`, `main.tf` | The CPU-high condition ("CPU is high", Google severity WARNING, missing data set to "inactive") | No tag. Missing data: "has no equivalent". The rule file said nothing about what happens when data stops. | Tag `gcp-severity:WARNING`. The description keeps "Google severity: WARNING" and "Documentation format: markdown". When data stops, New Relic now waits 10 minutes and then closes any open problem without opening a new one. | Google's "inactive" setting means missing data is not a problem. Before, that was dropped silently; now it is written as the closest New Relic setting, and the note says it fires later than Google would. The severity was lost before. |
| `ir/92147052606053.json`, `nerdgraph/92147052606053.json`, `main.tf` | The log-match condition | Nothing about how often it may notify. | A limit of 300 seconds between repeat notifications, in the description and as the violation time limit, plus a note. It is also labelled "needs log forwarding". | Google only notifies once every 5 minutes for a matching log; New Relic would have notified on every match. The label tells the person the logs must be sent to New Relic first, which was true before but unsaid. |
| `report.md` | The summary page of that conversion | One line per condition. | The log-match line carries the new note, and there is a new "Data path" table (2 native, 1 needs log forwarding). | Same facts as above, in the report people actually read. |

## Notification routing (pinned group `notifications/gcp`, 2 files)

| Pinned file | What it is | Old output said | New output says | Why the new one is more correct |
|---|---|---|---|---|
| `notification-plans/set-0001.json` | Routing set with the email channel "Ops mailbox" and the PagerDuty channel "Primary pager" | "Needs review: no", nothing left to do by hand. | "Needs review: yes", with two to-do items: the email address and the PagerDuty key are not exported by Google, so they must be typed in again. | Google hides secrets and addresses when it exports a channel. Saying "nothing to do" was wrong: a person had to find that out later. |
| `notification-plans/set-0002.json` | Routing set with Slack "#alerts" and the webhook "Ticket hook" | Same: no review, nothing to do. | "Needs review: yes". Slack needs a one-time sign-in by a Slack admin; the webhook token must be typed in again, with a hint that a ServiceNow or Jira address should use that New Relic connection instead. Labelled unverified where the token label is not confirmed. | Same reason. |

## Dashboard output (pinned group `gcp` in `dashboard-output-hashes.json`, 4 files)

All four files belong to the one dashboard "Ops overview" (`58944063589550`): its saved IR, the New
Relic JSON, `dashboards.tf` and `report.md`. Widgets converted in this fixture go from 11 to 14 of
18 (the dashboards read include some that are not emitted), and "not converted" in "Ops overview"
from 6 to 3.

| Widget | Old output said | New output says | Why the new one is more correct |
|---|---|---|---|
| "Errors" (logs panel) | "Not migrated: widget type not supported" | A table over the Log data: `SELECT timestamp, message FROM Log WHERE severity IN ('ERROR', ...) LIMIT 200`, with a note that logs must be forwarded and the project/resource choice is not migrated. | The panel is a list of log lines; New Relic can show that. A note replaces a gap. |
| "MQL chart" | "MQL not converted" | Still not converted; the text now adds that Google has ended MQL for new charts and the query must be rewritten. | Only the explanation is better; the outcome did not change. |
| "Error ratio" | "ratio queries are not converted" | A line chart: `filter(average(gcp.run.request_count), WHERE metric.response_code_class = '5xx') / average(gcp.run.request_count)`, with notes. | A ratio can be written as one query. See the warning below about "average" versus "sum". |
| "Open alerts" | "widget type not supported" | Still not converted, with the clearer reason that New Relic has no widget that charts a Google alert policy and a hint to chart the metric the policy watches. | Only the explanation changed. |
| "Custom metric" | "not one of the Google Cloud service metrics, the query would return no data" | A line chart `SELECT average(orders) FROM Metric`, labelled "needs custom ingest". The note says the person must send that metric to New Relic under that name. | The query is right once the customer sends the data; the label says so. The name is marked unverified. |
| Report page | "converted 11" and no data path section | "converted 14" and a Data path table (5 native, 1 needs custom ingest). | Consistent with the widgets above. |

## Saved-IR check for the other sources (`ir-before-grafana.json`, 4 Google files)

This test saves the converted form of every source's fixtures. Only the Google files differ: the
same two alert conditions as above (`ir/` and `nerdgraph/` for `253922000910239` and
`92147052606053`). AWS CloudWatch, the real CloudWatch sample and Dynatrace did not change by a byte.

## Reason-category check (`taxonomy_before_grafana.json`, 3 sentences)

This test lists every sentence the converters write as a reason, with the category the project
files it under. Two sentences are new and one is gone, all from the Google conditions above:

| Sentence | Status | Category |
|---|---|---|
| "EVALUATION_MISSING_DATA_INACTIVE has no equivalent ..." | Gone | was "approximate conversion" |
| "EVALUATION_MISSING_DATA_INACTIVE becomes loss of signal that closes open violations ..." | New | approximate conversion |
| "Google notifies for a matching log at most once every 300s ..." | New | approximate conversion |

No existing sentence changed its category.

## What looks wrong or could not be explained

1. **Ratio widget uses averages, not sums.** The new "Error ratio" query divides the average of the
   5xx requests by the average of all requests, while its own note says "sum(numerator) /
   sum(denominator)". The fixture gives no aggregation, so the code falls back to "mean". For a
   count of requests a ratio of averages is not the share of errors (it is inflated by how many
   series each side has). Treat this one chart as unreliable until the default is changed to a sum
   for counters. The alert-side ratio (not in these fixtures) uses sums, as intended. I did not
   change the code because it is a behaviour decision for the owner.
2. **The CPU-high saved IR says "notify: yes, 10 minutes" for missing data while the emitted
   setting is "do not open a problem, close open ones".** The New Relic files (JSON and Terraform)
   are right, they follow the second; the first field in the saved IR is only a leftover and does
   not reach New Relic. Low risk, but it reads as a contradiction.
3. **Logs panel is now counted as a converted chart.** It is a real query, but it is a list of log
   lines and depends on the customer forwarding logs, and the attribute names of forwarded logs are
   unconfirmed. The report says so. If you want such tiles counted separately, say so.
4. Nothing else looks like a regression. Native-only items (no custom, Ops Agent, Prometheus or log
   routes) are unchanged apart from the notes above.

## Verified and unverified

Verified here: the old and new outputs were both produced offline from the fixtures and compared
file by file; only the files listed above differ; after `--apply` (tried on a throwaway copy of the
repository) every pinned test passes and no expected-failure marker remains. Not verified: any
New Relic behaviour (nothing was sent to New Relic or Google Cloud) and the label and attribute names
marked unverified in the notes.
