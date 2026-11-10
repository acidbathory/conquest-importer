# Live alert firing test: a converted Grafana rule opens and closes a New Relic incident

Run 2026-11-10 (owner authorised for this task, account from `.env`, no secret recorded here). First time anything converted by this project fired. Nothing could notify anyone: no destination, channel, workflow or muting rule was created. Scripts: `scripts/live-test/grafana-sandbox/alert-fire/fire.py` (show, enable, send, drive, nrql) and `rules.json` (the synthetic rules, written in the style of `tests/fixtures/grafana`).

## Setup

Two synthetic Grafana rules, converted offline with `importer convert --source grafana`, applied with `importer apply` (policy `ckg-alert-fire-test`, incident preference PER_CONDITION, the project default; conditions created disabled):

| Grafana rule | PromQL / reduce / test | for | Converted NRQL | Window | Duration |
|---|---|---|---|---|---|
| `ckg-synth-cpu` | `avg(ckg_synth_cpu{ckg_test="true"})`, last, `> 80` | 2m | `SELECT average(ckg_synth_cpu) FROM Metric WHERE ckg_test = 'true'` | 60 s | 120 s |
| `ckg-synth-rate` | `sum(rate(ckg_synth_requests_total{ckg_test="true"}[5m]))`, last, `> 1.5` | 2m | `SELECT sum(ckg_synth_requests_total) / 300 FROM Metric WHERE ckg_test = 'true'` | 300 s | 300 s (120 s rounded up, as the note says) |

Read back through NerdGraph before enabling: query, threshold, operator ABOVE, CRITICAL, duration, occurrences ALL, value function SINGLE_VALUE, EVENT_FLOW, delay 120 s, fill NONE, no expiration (loss of signal off), violation time limit 259200 s: all as the converter emitted. Enabling (`enabled: true`) is the one update, done by a script that only touches manifest conditions named `ckg-`.

Data: one Metric API point per 10 s per metric in real time (not backdated), all with `ckg_test=true`. cpu gauge 30, then 95, then 30. Rate: delta `count` with `interval.ms=10000`, 1.2/s, then 3.0/s, then 1.2/s.

## Timeline (UTC, 2026-11-10)

| Time | Event |
|---|---|
| 04:08:34 | condition enabled, low phase starts (cpu 30, rate 1.2/s) |
| 04:08 to 04:13:41 | no incident (expected) |
| 04:13:41 | high phase starts (cpu 95, rate 3.0/s) |
| 04:15:00 | rate window 04:10 to 04:15 = 1.6 (above 1.5); signal seen by New Relic 04:17:48 |
| 04:18:00 | **ckg-synth-rate incident OPEN** (openTime 04:15:00), 4 min 19 s after the first high sample |
| 04:18:53 | **ckg-synth-cpu incident OPEN** (openTime 04:16:00; windows ending 04:15 and 04:16 both 95; the second seen 04:18:43), 5 min 12 s after the first high sample |
| 04:25:43 | recovery phase starts |
| 04:25:00 | last breaching window of both conditions (closeTime 04:25:00) |
| 04:26:00 | cpu window 73.3 (mixed minute, below 80) |
| 04:29:51 | **ckg-synth-cpu incident CLOSE**, 4 min 08 s after recovery started |
| 04:30:00 | rate window 04:25 to 04:30 = 1.4 (blend, below 1.5); seen 04:32:54 |
| 04:33:04 | **ckg-synth-rate incident CLOSE**, 7 min 21 s after recovery started |
| 04:37:52 | data stops |

`NrAiIncident` rows: 4 (open and close for each condition, priority critical, titles "Metric query result is > 80.0 for 2 minutes on 'ckg-synth-cpu'" and "... > 1.5 for 5 minutes on 'ckg-synth-rate'"). No incident in the 5 minute low phase.

`NrAiSignal` (queryable, one row per window per condition: `signalValue`, `aggregatedDataPointsCount`, `endTimestamp`, `serverTime`): rows for cpu arrive 159 to 182 s after the window end (aggregation delay 120 s plus 40 to 60 s of processing), 5 or 6 points per 60 s window as sent. Open and close events follow the signal that decides them by 10 to 12 s for cpu and rate.

## Findings

1. Match: a converted rule opens an incident when the metric crosses the threshold for the duration and closes when it recovers. Threshold, operator, window, duration and `ALL` occurrences did what the converter said.
2. Latency is the main gap and was not documented. Detection time is about: windows needed for the duration + aggregation delay (120 s default) + about 50 s. The cpu rule (60 s window, 2 min) opened 5 min 12 s after the first breaching sample; close took 4 min 08 s. Grafana with a 1 minute evaluation interval would open after about 2 to 3 minutes. Fixed as a note on every pending period (below).
3. Rate rule: window alignment decides everything. The 300 s window 04:10 to 04:15 held 79 s of high data and 221 s of low, and still computed 1.6 (above 1.5), so the incident opened on a window that was mostly baseline. The converter's note "Grafana looks at the last 300s at every evaluation; New Relic evaluates separate 300s windows" is right, and the effect can be a premature open and a late close (7 min 21 s). The counter conversion `sum(x) / 300` worked on the Metric API delta counts (28 points per window, 1.12 to 2.8 as expected).
4. Not exercised, so the notes remain unverified: loss of signal and no-data (the conditions have no expiration, because the rules had `noDataState: NoData`; with no expiration New Relic never opens an incident when data stops, which matches the note "New Relic does not raise a separate no-data alert"; whether an open incident then stays open was not tested), `noDataState: Alerting` (expiration with `openViolationOnExpiration`), "restarts the count when a window has no data", multiple series, fill options.
5. Observation without explanation: after data stopped at 04:37:52, `NrAiSignal` rows for cpu ended at the window ending 04:35:00 (checked 04:41), although the windows ending 04:36 and 04:37 contained data. Possibly signal rows lag behind or the last windows are dropped; not investigated.
6. Rollback by the project command removed the two conditions and the policy (see the manifest); nothing else was touched.

## Fixes

- The `N_PENDING` note now states that the incident opens later than in Grafana and by how much (test first: `tests/test_grafana_alert_latency.py`). Only note text changed, so the `convert/grafana` hash group changes; no golden file or baseline was touched.

## Cleanup

Created and deleted (guids recorded in `out/live-run/alert-manifest.json`, git-ignored): policy 8313881, conditions 69646067 and 69646068. The metric points (`ckg_test=true`, about 450 per metric) stay in the account until retention removes them.
