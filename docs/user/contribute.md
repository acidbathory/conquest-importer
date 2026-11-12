# Help us make progress

Conquest Importer converts alerts and dashboards from other monitoring tools into New Relic. It is
tested on public sample content, not on customer estates, and the biggest limits now come from
information we do not have. Four things would move it forward more than any code change.

Where we stand on public sample content (alerts and dashboard widgets that convert to a working
query, placeholders not counted): Google Cloud about 72%, Datadog about 53%, AWS about 35%, Grafana
about 33%, Azure about 25%, Dynatrace about 2%. Nothing is verified against a live account yet, so no
conversion is marked "exact". These are not customer results.

## 1. Share what failed on a real estate (low effort)

Run the importer on your own export, offline. Then share only the **counts and reasons**, which carry no
names:

```sh
uv run importer convert-dashboards --source grafana --in <your export> --out out/mine
uv run importer scoreboard --in out/mine --out out/mine-scoreboard
```

Post the contents of `out/mine-scoreboard/scoreboard.md`, or the output of `importer stats`, in a
[feedback issue](https://github.com/acidbathory/conquest-importer/issues/new/choose). That shows us which blockers matter in real use.

Do **not** attach raw exports, dashboards, queries or screenshots to a public issue. They can hold
names, hostnames, account ids and secrets. If you want to give us a scrubbed export itself, ask for a
private channel first (see [SECURITY.md](../../SECURITY.md)).

## 2. Run the hints step against a real Prometheus (medium effort)

Many Grafana and Google Cloud widgets are blocked because a PromQL query does not say whether a
metric is a counter or a gauge, or which labels identify one series. The `grafana-prom-hints` command
reads that from a Prometheus, Mimir or Grafana Cloud stack with GET requests only:

```sh
uv run importer grafana-prom-hints --prometheus-url http://localhost:9090 --out hints.json
```

(It also accepts `--datasource` for a Grafana data source, and `--metrics-from` for the dashboards to scan.)
The output holds metric names, types and label keys, not values. Review it, then share the counts of
how many metrics got a type. This is the biggest single unlock we know of, and it has never run against
a stack with real metric metadata.

## 3. Check names and queries in a New Relic sandbox (medium effort)

Every conversion is "approximate" because nothing was run in New Relic. If you have a sandbox account
that already receives data from your tools, run the check for your source and report what fails:

```sh
uv run importer check-datadog-data --alerts out/datadog --nr-account-id <id>
uv run importer validate --help
```

Equivalent `check-azure-data`, `check-gcp-data`, `check-grafana-data` and `check-dynatrace-data`
commands exist. Use a key with read access. The importer never writes it to any output.

## 4. Review our NRQL refusals (expert review)

If you know NRQL well: tell us where we refuse a conversion that has a correct form. Open an
[NRQL review issue](https://github.com/acidbathory/conquest-importer/issues/new/choose). Examples we would most like an answer on:

- Alert conditions that compare to an earlier period (Datadog `change()`).
- Baseline conditions for anomaly monitors.
- Summing a gauge per series without a subquery.
- Rate of a gauge stored as a running total.

## Smaller things

- Dynatrace and Azure experts: public Dynatrace alert content is thin, and Azure baseline alerts use
  a tag-lookup pattern we cannot convert.
- Pick a blocker from a scoreboard report and fix it. Start with [CONTRIBUTING.md](../../CONTRIBUTING.md).
