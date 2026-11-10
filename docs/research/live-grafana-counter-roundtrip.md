# Live counter round trip: Prometheus counters and node_exporter in New Relic, read back with converted NRQL

Run 2026-11-10 (owner authorised for this task). Account and keys come from `.env`; none is recorded here. Every point carries `ckg_test=true`. Scripts: `scripts/live-test/grafana-sandbox/synth_counter.py`, `node_exporter_roundtrip.py`, `node_compare.py` (helpers in `nrcommon.py`). The NRQL below is the converter's own output (`convert_query`), with only a `SINCE`/`UNTIL` pair appended; PromQL selectors carry `{ckg_test="true"}` (Part 1) or `{job="ckg_node"}` (Part 2), which become the `WHERE`.

## Part 1: synthetic counter

Data: 60 steps of 10 s (590 s), ending now, sent in one batch (backdated, not real time). `ckg_synth_requests_total` is a Metric API `count` with `interval.ms=10000`: code=200 gets +10 per step (1.0/s), code=500 gets +2 (0.2/s), total 1.2/s. `ckg_synth_requests_gauge_total` is a `gauge` holding the cumulative value. Ingested: 120 points per series, sums 600 and 120, as sent.

| PromQL | Converter NRQL (chart / stat) | Expected | Observed | Verdict |
|---|---|---|---|---|
| `rate(x[5m])` | `SELECT rate(sum(x), 1 second) FROM Metric WHERE ckg_test = 'true' TIMESERIES AUTO` | 1.2/s | every 20 s bucket 1.2 (AUTO chose 20 s); with `TIMESERIES 1 minute` all 9 buckets 1.2 | match |
| `rate(x[5m])` as stat | same without `TIMESERIES` | 1.2/s | 1.218 for a 591 s window (720 / 591) | match within window rounding (the rate divides by the query window, not the data span) |
| `sum(rate(x[5m])) by (code)` | `... FACET code LIMIT MAX TIMESERIES AUTO` | 1.0 and 0.2 | 1.0 (29 buckets) and 0.2 (29 buckets) | match |
| same as stat | `... FACET code LIMIT MAX` | 1.0 and 0.2 | 1.0152 and 0.203 | match within window rounding (x 600/591) |
| `increase(x[1h])` | `SELECT rate(sum(x), 1 hour) ... TIMESERIES AUTO` | steady state 1.2 x 3600 = 4320 | 4320 in every bucket | match for steady state; see note |
| `increase(x[1h])` as stat | `... rate(sum(x), 1 hour)` no TIMESERIES | Prometheus: 720 (only 10 min of data) | 4385.8 (720 / 591 x 3600) | mismatch by design: NRQL extrapolates the window rate to one hour, Prometheus counts the increase seen in the window |
| `irate(x[5m])` | identical text to `rate` | steady data: 1.2/s | 1.2 in every bucket | match on steady data; differs on spikes (bucket average, not last two samples); note added |
| same NRQL on the gauge form | `rate(sum(x_gauge_total), 1 second)` | 1.2/s | 4.2 up to 71.4 per bucket, stat 30.96 (code 200) | mismatch: summing a cumulative gauge adds the running total; the gauge form needs `derivative`, which the converter does not write |

Bucket-size checks on the same data (10 s scrape interval, true rate 1.2): 5 s buckets give 0 and 2.4 alternating (118 buckets, mean 1.2); 15 s buckets give 0.8 and 1.6 (a 15 s bucket holds one or two 10 s points); 10 s, 20 s, 60 s, 5 min buckets give exactly 1.2. `TIMESERIES MAX` gave 365 buckets from 0 to 7.43. The mean always stays right; only the shape is wrong.

Root causes found (measured):
- Bucket size vs scrape interval: `rate(sum(x), 1 second)` per bucket is exact only when the bucket is a multiple of the scrape interval. Prometheus avoids this by making the rate window at least four scrape intervals; the converter's `TIMESERIES AUTO` ignores the `[5m]` and the scrape interval. Fixed here by a note (see Fixes).
- Scrape interval assumption: the delta `count` carries `interval.ms`, so rate and sum are exact for any bucket that is a multiple of it. No assumption is hidden in the converter.
- Window division: stat widgets divide by the query window, so a window longer than the data lowers the rate (591 s window, 590 s of data: +1.5%; a 660 s window over 600 s of data: -9%).
- First bucket partial: the first bucket of a chart holds fewer deltas (in Part 2, 3 of 4 scrapes, value 6.79 against 9.94); the last bucket likewise. Not a converter issue; Grafana shows the same edge with a short range.
- Units: seconds and per hour behave as documented (`1 second` -> /s, `1 hour` -> x3600).
- Cumulative gauge: the gauge form of a counter has no correct `rate(sum())`; a raw cumulative graph has no equal (known).

## Part 2: real node_exporter

node_exporter 1.12.1 darwin-arm64 from the official GitHub release over HTTPS into a new temp directory; the tarball sha256 matched the release's `sha256sums.txt` (same release origin, so integrity against transfer errors, not against a compromised release). Bound to 127.0.0.1:9100 (checked with `lsof`), no sudo, no install. 41 scrapes every 15 s (600 s); raw text kept in `/tmp/ckgwork/raw.jsonl`. Converted by the datapath rules (23 metrics, 5,482 points, 22 network devices, 48 cpu series): counters to delta `count` with `interval.ms` from consecutive scrapes (15,000 ms), gauges to `gauge`, labels to attributes plus `job=ckg_node`, `instance`. node_exporter stopped and the temp directory deleted afterwards.

macOS differences: there is no `node_memory_MemAvailable_bytes`/`MemTotal_bytes`; the Darwin names `node_memory_total_bytes` and `node_memory_free_bytes` were used with the same query shapes as the Node Exporter Full panels.

Two expectations per row. `NR`: what the sent points must give (sum of deltas per 60 s bucket / 60; mean or sum of samples per bucket). `Prom`: what Prometheus would show from the raw scrapes (whole-window rate; latest sample per series, then the aggregate). Chart rows run with `TIMESERIES 60 SECONDS` anchored at `SINCE` and compare interior buckets; stat rows use a window from the first to the last scrape.

| PromQL | Converter NRQL | Observed vs NR | Observed vs Prom | Verdict | Root cause |
|---|---|---|---|---|---|
| `sum by (mode) (rate(node_cpu_seconds_total[5m]))` chart | `SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric WHERE job = 'ckg_node' FACET mode LIMIT MAX TIMESERIES 60 SECONDS` | exact (4 modes, all buckets) | per bucket differs: Prometheus smooths over 5 min, the bucket over 60 s (real load changes) | match (math), shape differs | bucket 60 s vs 5 m window |
| same, stat | same without `TIMESERIES` | exact | within 0.30% (idle 9.912 vs 9.941) | match | window is 1 s longer than the data |
| `sum by (instance) (rate(...{mode!="idle"}[$__rate_interval]))` | `... AND mode != 'idle' FACET instance ...` | exact (chart and stat) | stat within 0.30% | match | `$__rate_interval` becomes the bucket (existing note) |
| `rate(node_network_receive_bytes_total{device!="lo"}[5m]) by device` | `... AND device != 'lo' FACET device ...` | exact for all 22 devices | stat within 0.30% | match | as above |
| `node_memory_total_bytes - node_memory_free_bytes` | chart `average(a) - average(b)`; stat `latest(a) - latest(b)` | exact | chart: bucket mean vs last sample, up to 0.4%; stat exact | match | chart averages the samples of a bucket |
| `node_memory_free_bytes` | `average(...)` / `latest(...)` | exact | stat exact; chart is a bucket mean (free memory moves a lot on macOS) | match | mean vs last sample |
| `100 * (1 - sum(free) / sum(total))` | `100 * (1 - (sum(a) / sum(b)))` | exact | within 1% | match | both sums add the same number of samples, so the ratio cancels |
| `avg(node_load1)` / `node_load1` | `average(node_load1)` chart; stat `average` / `latest` | exact | stat of `node_load1` exact (2.809); `avg()` stat 3.02 vs 2.81 (mean of the window, not the latest) | match (note already says so) | whole-range mean |
| `node_filesystem_avail_bytes{fstype!="tmpfs"}` | chart `average(...)`, stat `latest(...)`, no FACET | chart exact (merges 9 mounts) | stat: one arbitrary mount returned (0.5 GB) while Prometheus shows 9 series | mismatch by design | series merged (existing note: add a FACET) |
| `sum by (mountpoint) (node_filesystem_avail_bytes)` | `SELECT sum(...) FACET mountpoint ...` | exact against NR math | chart 4.01x (4 scrapes per minute), stat 41x (41 scrapes) | **mismatch** | `sum()` of a gauge adds every sample (existing note); exact count = number of scrapes in the bucket |

Overall: for the 10 expressions run as chart and stat (20 runs), the NRQL result equals the arithmetic on the sent points to floating-point precision in 19 of 20; the one exception is the merged `latest()` on a multi-series gauge, where "latest" is not defined. Counter rates and increases are right in the stat form within the rounding of the window, and in the chart form per bucket.

## Fixes made

1. `irate` produced the same NRQL as `rate` with no remark. Now a note: "irate shows the average rate of each bucket, not the last two samples" (test first).
2. Rate, irate and increase charts with an automatic bucket now carry a note that the bucket may differ from a multiple of the scrape interval and makes the rate swing (5 s buckets on 10 s data: 0 and 2.4 around 1.2). Not added when a fixed bucket is set or for stat widgets (test first).

Only notes changed; the NRQL text did not. The `convert/grafana` hash group changes (new notes); no golden file was touched.

## What remains

- `sum()` of a gauge is wrong by the number of samples per bucket and the converter cannot know the scrape count or the series count; the exact form needs a nested query over `latest()` per series, which a chart cannot express here. The measured factor (4.0x per 60 s bucket at 15 s scrapes, 41x for the whole 600 s range) is the evidence for the existing note.
- The gauge form of a counter (`derivative` over the cumulative value) is not converted.
- `increase(x[1h])` over data shorter than an hour is a rate times an hour, not the increase seen.
- Not tested: Prometheus remote-write ingestion itself (the points were sent in the documented delta form by script), counter resets in live data (the script treats a drop as a reset; none occurred), more than one scraped target.
- New Relic received 240 synthetic and 5,482 node_exporter points, all tagged `ckg_test=true`; nothing was deleted or changed.
