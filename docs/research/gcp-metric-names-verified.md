# Google Cloud metric names, checked on real data

Checked on 2026-11-05 in a test Google Cloud project linked to a New Relic sandbox with Workload
Identity Federation (no keys, 5 minute polling). Test resources: one `e2-micro` VM, a Pub/Sub
topic and subscription, and a Cloud Storage bucket, with a few messages and object reads and
writes. The test resources were destroyed once the check was done.

## What was confirmed

- **57 metric names arrived**, listed in `src/importer/sources/gcp/gcp_verified.json`.
  First data appeared about 4 minutes after linking.
- **The naming rule holds:** `gcp.` plus the service and the metric path with dots. Examples:
  `gcp.compute.instance.disk.read_bytes_count`, `gcp.pubsub.topic.send_request_count`,
  `gcp.storage.api.request_count`.
- **No suffix is added.** The earlier assumption that counters get `_count` was wrong; Google's
  counter names already end in `_count` where they should.
- **Distribution metrics get percentile series:** `gcp.pubsub.topic.message_sizes` also arrives
  as `.p50`, `.p95` and `.p99`. A percentile alert converts to the matching series.
- **Values match Cloud Monitoring.** Pub/Sub sends: 5 in both. Acknowledgements: 1 in both. Disk
  write operations: 871 in New Relic and 877 in Google. Storage requests: 21 and 23. The small
  differences are the minutes between polls.
- **A converted alert runs.** Queries produced by the importer for a Pub/Sub backlog, topic
  sends, disk writes and a p99 message size all returned the expected data.

## Attribute names

- Resource labels are `gcp.<label>`: `gcp.zone`, `gcp.instance_id`, `gcp.topic_id`,
  `gcp.subscription_id`, `gcp.region`. The project is `gcp.projectId`.
- Metric labels are `metric.<label>`: `metric.method`, `metric.response_code`,
  `metric.delivery_type`, `metric.instance_name`.
- User labels are `gcp.labels.<key>`.
- **These attributes are not on every metric.** Event-style metrics (`ack_message_count`,
  `api.request_count`) carry only `fullResourceName` and the project, with no subscription or
  bucket attribute. Gauges such as `num_undelivered_messages` carry `gcp.subscription_id`. If a
  converted query that filters or facets on a resource label returns nothing, filter on
  `fullResourceName` instead. The report says this on every conversion that uses a label.

## What did not arrive

`gcp.compute.instance.cpu.utilization`, `cpu.usage_time` and `uptime` are in New Relic's mapping
table, and Google had data for all three on the test VM, but none reached New Relic in over an
hour. The reason is unknown. Other Compute Engine metrics (disk, firewall, quota, reserved cores)
did arrive. An alert on VM CPU utilization may therefore return no data. The importer says so on
that conversion. Worth raising with New Relic support.

Not tested: Cloud Run, Cloud SQL, GKE, load balancing, BigQuery and the other services.
Metrics from those still carry the "not confirmed" note.

## Second run (2026-11-06): converted conditions against live data

A fresh link (new workload identity pool) and a new VM, topic and subscription gave the same
result: `cpu.utilization` and `cpu.usage_time` did not arrive (checked for 40 minutes), although
the Cloud Monitoring API returned both for the same VM. `cpu.reserved_cores` arrived. Also missing:
`gcp.pubsub.topic.send_message_operation_count` (Google has it; `send_request_count` arrived).

Five conditions converted from real alert policies were created in the sandbox (disabled) and
their queries run against the data:

- Counter metrics arrive as one change per 5 minute poll (25 publishes showed as a total of 25;
  80 as 80 in one bucket). A 60 second window sees a point about one time in five, so the
  converter now uses a window of at least 300 seconds and scales the threshold: a rate per second
  is multiplied by the window, a change per period by window divided by period.
- A `sum()` over a metric that does not exist returns 0, not an error, so a condition on a missing
  metric looks healthy and never fires. `max()` and `average()` return null.
- Undelivered messages: 25 against a threshold of 5, so that condition would fire.

## Cost of the test

An `e2-micro` VM (about $0.01 an hour) for a few hours, and Pub/Sub and Storage at a volume that
costs effectively nothing. Google bills New Relic's polling reads to the project.
