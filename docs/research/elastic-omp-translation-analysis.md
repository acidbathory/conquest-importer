# Translation engine of the Elastic observability migration platform: what we can learn for a New Relic target

Status: research note, written 2026-11-11 on branch `research/elastic-translation`. Offline: nothing was run against Grafana, New Relic, Datadog or any cloud. Evidence tags: READ means I read the upstream code or its docs in this task; OURS means I read our repository; KNOWLEDGE means public vendor behaviour I know and did not re-read today (treat as a lead to verify with the researcher agent before relying on it); UNVERIFIED means a design idea nobody has run against New Relic.

## 0. Clean-room statement

The upstream project (`elastic/observability-migration-platform`) is under the Elastic License 2.0. This repository is Apache-2.0. I fetched the tarball into a scratch directory outside the repository, read it with read and search tools only, ran none of it, and deleted the scratch directory at the end. Everything below is a description in my own words. No code, docstring, comment, rule list, mapping table, regex, prompt, test fixture or document text was copied; where a function or module is named it is a short identifier used to say where a behaviour lives. The NRQL designs in section 4 are written from our own census and cookbook plus public New Relic behaviour, not derived from their rules. Section 8 says what is safe to reproduce from public sources instead. A later reader should treat this note as an opinion about design, and should not "check it against" the upstream source while implementing; implement from the NRQL design and our own tests.

## 1. Their engine in two pages: data model and pipeline

### 1.1 Shape of the package

One Python package with four layers (READ):

1. Source adapters, one for Grafana and one for Datadog. They own all vendor JSON: extraction from files or the vendor API, normalisation, query parsing, planning, translation, verification packets, rollout files.
2. A shared core: typed asset classes (dashboard, panel, query, visual, control, alert), a shared status vocabulary, metric-name mapping, field-capability probing of the target, comparators and a "parity oracle", and reporting.
3. One target, Kibana, with an emitter that turns the shared dashboard model into the Kibana Dashboards API payload and an alerting emitter for Kibana rules.
4. Two command line surfaces: a source-agnostic one and one per source.

The design rule they state: raw vendor JSON never leaves the adapter; shared code sees typed objects. This is the same split as our `sources/<x>` versus `core/`.

### 1.2 Data model

- Dashboard model (their "DashboardIR"): title, filters, time range, refresh, a tree of panels (a section holds children), a list of controls, and side lists for alerts, annotations, links and transformations that are carried for review rather than emitted. Each leaf panel holds a visual description whose "presentation" says which of a few target block kinds it is (query-backed, Lens, markdown, links, image).
- Query model, split in three on purpose: the semantic intent of the query (source language, source text, family such as simple metric, range aggregation, nested aggregation, binary expression, topk, histogram, join; metric, range function and window, outer aggregation, grouping labels and mode, label filters, binary operator, output shape such as time series, single value, table, event rows); a contract that lists what must be true of the target for the translation to be exact (required fields with role and type family and metric kind, data invariants, runtime features, degradation policy, hints on how to fulfil a gap); and a plan that holds the rendered target query and index. The contract is evaluated against a snapshot of the live target (field capabilities, index patterns, runtime features). The class of exactness is a value attached to the contract, and they only use one value, "exact if the contract is met".
- Status vocabulary shared by both sources: translated; translated with warnings; manual required; not feasible; blocked; skipped. Source-specific words are mapped onto it.
- A second axis, orthogonal to status, called target binding: for every label the final query emits, was the target field verified in live capabilities, asserted by a rule pack, guessed, or missing. Binding never changes status. Post-upload evidence (a series that is all null, a control with no options) is what moves the verification gate.
- A verification gate with three colours (green, yellow, red) computed from status, semantic-loss markers found in reason and note text, runtime rollups (query error, empty result, null series, control unbound), and the result of comparing source and target values when both can be run.
- Everything carries a stable rule id. Planning and translation run through registries of small rules with a priority; the id of every rule that fired is recorded on the result, and the registries can be exported as a catalogue for extension authors.

### 1.3 Pipeline (Grafana)

1. Load rule packs and plugins, build a schema resolver, and (if a cluster URL is given) discover field capabilities and runtime features.
2. Extract dashboards (files or API).
3. Pre-passes on the whole dashboard: template-variable classification (literal variables are substituted before any query is read), curated per-dashboard pack matching.
4. Per panel: map the panel type, then translate every query through the phased rule engine (preprocessors, classifiers, translators, postprocessors, validators). The classifier phase can stop the run with "not feasible"; later phases only run for feasible queries.
5. Fuse compatible targets of one panel into a single query shape; derive controls from variables; lay out; build the dashboard model; derive the native payload from the model.
6. Optional: run the emitted queries against the cluster, apply a short list of safe rewrites, and turn what still fails into manual items; compare values to the source when it can be queried; upload; browse the result and record per-panel evidence.
7. Write the review files: native payloads, model dumps, report, manifest, verification packets, rollout plan, a schema-change report, and a telemetry contract that lists the fields the target must receive.

An optional last resort exists for queries the rule engine refuses: a language-model suggestion, accepted only if it does not read a column synthesised from an unresolved variable. I do not recommend anything like it for us (section 5, rejected idea).

### 1.4 Pipeline (Datadog)

Normalise the dashboard into a flat widget list (groups recursed, per-request and per-widget queries gathered, legacy and new request shapes both read); plan each widget (choose query-backed, Lens, markdown, image, group or blocked); preflight against target field capabilities; translate metric queries, formulas and log searches through registries; assemble the same dashboard model; emit. Monitors go through a separate pipeline: classify the monitor type, parse the query string, translate to a target query when it is in a small provably-safe subset, map thresholds and options, record semantic losses, and emit a rule payload only for the subset the target can validate.

## 2. Query translators: how they parse, rewrite and refuse

### 2.1 Parsing

| language | their parser | note |
|---|---|---|
| PromQL | an external parser package that wraps a native (Rust) PromQL parser, plus a text pre-pass that strips Grafana macros and replaces variables by placeholders so the real parser accepts the text, then restores them | READ. Fragments (a small tree of "what is this part") are built from the parser's nodes. A regex fallback no longer drives translation. |
| LogQL | two anchored regular expressions, nothing else | READ. Only a stream selector with at most one line filter, and a sum or count of `count_over_time`, are recognised. Everything else is refused. |
| Datadog metric query | hand-written splitter and recursive scanner; boolean scopes and `IN` lists; function chain; legacy forms | READ. Returns a typed result with diagnostics and a "lossless" flag, so a degraded parse is a first-class state. |
| Datadog formula | hand-written tokenizer and precedence parser into a small tree (reference, number, string, unary, binary, function call) | READ |
| Datadog log search | a Lark grammar plus a transformer | READ. Diagnostics distinguish parse error, skipped characters and a boolean fallback. |

Ours (OURS): `sources/grafana/promql_ast.py` is a hand-written tokenizer and precedence parser with length and depth caps; 857 of 857 census expressions parsed. We have no LogQL parser beyond what `reuse.py` and the alert translator recognise, and no Datadog parser (removed).

Lesson: their "parse result with diagnostics and a lossless flag" is a cheap, good idea (section 5, item F). Their reliance on an external native parser is not something we need, but it could serve as an independent oracle for our own parser in tests, if its licence allows a development-only dependency (check before adding; not done here).

### 2.2 Rewrite model

They do not rewrite text. They parse to fragments, classify the fragment into a family, and each family has a translator that writes the whole target query. Families I saw: scalar, simple metric, simple aggregation, range aggregation, scaled aggregation (arithmetic with a constant), nested aggregation, topk or bottomk, histogram quantile, binary expression (co-located or not), join, uptime (`time() - metric`), label replace, label join, log stream, log count. After the family translator, postprocessors add the sorting, rounding and clamping wrappers that the parser peeled off; validators then check structure (a structural oracle that parses the emitted pipeline and checks aliases and stages) and variable hygiene.

Notable design facts (READ):

- Counter or gauge is decided from a ladder: operator pin, naming suffix, live field capability, profile default. A counter function applied to a field the live target stores as a gauge is rewritten to the gauge analogue (change over the window divided by its length) with a warning; the same warning is raised when the source says "counter" and the target says "gauge".
- Rate semantics were found to depend on the bucket the chart uses. A window narrower than the bucket reads high by roughly the ratio; they fixed it by not emitting a window at all. A rate collapsed to a single number from the last bucket reads wrong because the last bucket is incomplete; they take the one before it. Both were found by running against the real source at several dashboard time ranges, not by reading documentation.
- `agg(A or B)` is refused: aggregation does not distribute over a union, so any rewrite silently drops an operand. They handle only three narrow cases: the same metric with two windows, an operand that is provably absent, and the `or vector(0)` null-to-zero idiom.
- Group-left joins are accepted only for the "info metric" idiom (metric name ends like an info series, plain selector, value always one), where dropping the join does not change numbers; the enrichment labels then become grouping dimensions on the main metric if they can be shown to exist. Any other join shape is refused with a reason that names which condition failed. The decision also depends on whether the target evaluates vector matching natively, which they probe.
- Comparison operators are filters: against a scalar they become a row filter before or after the aggregation depending on shape; between two vectors they become a conditional expression.
- Where the target can run PromQL itself (a PROMQL command in ES|QL), compatible queries are passed through as text and only labels and variable bindings are rewritten; the rule engine is the fallback when the target lacks the feature or a label does not exist. This is the largest structural difference from us and is discussed in 4.1.
- `histogram_quantile` becomes a percentile over the histogram field when the shape is the grouped-by-bucket form or the bare rate form, and is refused when an outer aggregation drops the bucket boundary label.

### 2.3 Classes of constructs they refuse

From the code and the docs (READ): subqueries; `offset`; metric-name introspection; `without` grouping (ES|QL grouping is positive); `absent`, `absent_over_time`, `changes`, `resets`, `timestamp`, `count_values`, `group`, `stdvar`; vector matching that is not the info-metric idiom (when the target does not evaluate it natively); an aggregation wrapping a union or a non-linear binary operation; `label_join` and `label_replace` when the source label does not survive the preceding aggregation; histogram quantile with the boundary label aggregated away; aggregation of a ratio that is not a sum-over-count pair; any function not in their list ("requires manual redesign"). LogQL: everything except the two shapes in 2.1. Datadog: legacy `top`, `timeshift`, `derivative` in formulas; anomalies, forecast, outliers; hostmaps with no grouping; grouping by a template variable; scope-wide `$scope` variable.

Every refusal carries a written reason, and the docs state that all 87 refused PromQL packets in their 1647-packet corpus have a reason and a red gate, so none is silent (READ). They also state a deliberate refusal: the bare-rate histogram quantile "per label set" is not guessed at, because choosing a grouping the source did not state produces plausible wrong numbers. This matches our "refuse rather than approximate silently" rule.

### 2.4 Classification, confidence and verification

- Status: the six-value vocabulary above. Feasibility is "invariant to whether a cluster is reachable": a query that translates but whose data has not arrived is a data-readiness warning, never "not feasible". Good principle, and ours (`data_check`) already separates the two.
- Confidence: a crude number (0 when refused, high when no warnings, medium otherwise; lower when a metric-name map was applied). The real signal is the warnings list plus the contract.
- Verification: (a) structural oracle on the emitted text; (b) run against the live target and classify failures as "data not yet there, self-heals" versus "query is broken"; (c) a parity oracle that runs the source query natively and the translated query on the same window, aligns series by canonicalised label set, and reports a verdict; the middle verdict "shape matches, values do not" has a hard relative-error ceiling (25 percent) above which it is a failure; (d) comparators with tolerance bands for row counts, scalars and column overlap; (e) a browser render audit; (f) for Datadog a parity rig that seeds identical synthetic data into both systems.

## 3. Construct comparison: theirs against ours

Our column is from `docs/research/promql-to-nrql-census.md` and `src/importer/sources/grafana/dashboard_convert/promql.py` (OURS): "writes" means the converter writes it today; "design" means the census gives an NRQL pattern that is not built; "none" means census verdict 4. Their column is READ. "Native" means they pass it to the target's own PromQL engine when that is available; that path has no counterpart for us unless New Relic's PromQL support can be used (see 4.1).

| construct | theirs | ours | note |
|---|---|---|---|
| selector with `=`, `!=`, `=~`, `!~` | writes (also native) | writes `=`, regex subset; `!=` and `!~` code 2 | single-quoted strings and upper-case aggregators are our parser gaps, not features |
| `rate`, `irate`, `increase` | writes; gauge analogue when target says gauge | writes | counter typing by name is our weak point |
| `delta`, `deriv` | writes | design | |
| `*_over_time` (avg, count, last, max, min, present, sum) | writes | design (`SLIDE BY`) | `stddev`/`quantile_over_time` not in their list either |
| `sum`, `avg`, `min`, `max`, `count` with `by` | writes | writes | |
| `stddev`, `quantile` | writes (std-dev, percentile) | design (code 4 for stddev) | |
| `stdvar`, `group`, `count_values` | refuses | none | |
| `topk`, `bottomk` | writes (two modes by panel type) | `topk` writes; `bottomk` none | NRQL ordering has no ascending form (census) |
| `without` | refuses | none | |
| nested aggregation | writes (two-stage) | design (subquery) | |
| `histogram_quantile` | writes (two shapes) | writes the grouped shape | |
| arithmetic between metrics | writes when co-located, shared grouping | writes inside one SELECT | |
| comparison as filter | writes (scalar and vector) | design (subquery with `WHERE`) | 108 of 412 census panels |
| `bool` modifier | writes | design | |
| `and`, `unless` | partial | design (`IN` / `NOT IN` subquery) | |
| `or` | narrow cases only | none | they, too, refuse the general union |
| `or vector(0)` | strips with warning | design | |
| `on`, `ignoring` | native or info idiom only | design (semi-join) | |
| `group_left`/`group_right` | info idiom only | none | 67 expressions in our corpus |
| subquery | refuses | design | |
| `offset` | refuses | design (`COMPARE WITH`) | |
| `absent`, `changes`, `resets`, `timestamp` | refuses | none (alerts: signal-loss condition) | |
| `label_replace`, `label_join` | writes if label survives | design (FACET math) | |
| `clamp*`, `round`, `abs`, `ceil`, `floor`, `sqrt`, `exp`, `ln`, `log*`, trig, `sgn` | writes (post-step) | `clamp_min` design; math design | |
| `sort`, `sort_desc` | writes (post sort) | design (`ORDER BY`, table only) | |
| `scalar`, `time` | writes (wrapped, uptime family) | design | |
| `$__rate_interval`, `$__interval`, `$__range` | substitutes | writes (builtin tokens) | |
| `${var}`, `$var`, regex variables | control binding, late-bound `by($var)` | variables module; `by($var)` not built | see section 7, item 9 |
| LogQL stream and `count_over_time` | writes | writes (rate, lines) | both refuse the rest |
| LogQL line filters beyond one, parsers (`json`, `logfmt`, `pattern`), `rate`, `bytes_over_time`, `sum by (label)` over parsed labels | refuses | partly | see 4.5 |
| Datadog `space:metric{scope} by {tag}` | writes | not built | section 6 |
| Datadog `rollup`, `fill`, `as_rate`, `as_count`, `per_second` | writes with warnings | not built | |
| Datadog formulas (arithmetic of named queries) | writes, three layers | not built | |
| Datadog `timeshift`, `top`, legacy `derivative` | refuses | not built | |
| Datadog `anomalies`, `forecast`, `outliers` | refuses (manual) | not built | |

## 4. What they do that we do not, and that fits an NRQL target

Each item says: their idea in my words, why it is valid for NRQL, and a design written from NRQL.

### 4.1 A passthrough tier for queries the target can run itself

Their idea: if the target speaks the source language, send the text and rewrite only the names. Valid for us? New Relic documents a Prometheus-compatible query API and a dashboard translator endpoint that takes Grafana JSON and returns widgets (our `docs/research/nr-promql-baseline.md` measured 398 of 400 expressions translated, 70 that we do not convert). So New Relic itself is a translator we can call, not a target that evaluates PromQL inside NRQL. Design (UNVERIFIED as a product decision): treat the vendor translator as a comparison oracle and a fallback tier, never as the default. Concretely: for each expression our converter refuses, record whether the vendor translator returns NRQL; if it does, run both candidates where we can (ours when present) read-only against the same window and attach the vendor text only as a "suggestion" in the report, flagged approximate, never emitted unattended. This gives a ranked list of which of our refusals are worth building. The baseline note already holds the numbers; what is new is making it a standing report column. Check terms of use of the endpoint before wiring it into an unattended path.

### 4.2 Conditional exactness (a contract of data invariants)

Their contract object says: this translation is exact if these target facts hold. For NRQL the facts are concrete and mostly about how Metric data is stored:

- the metric is a delta `count` (type is stored per sample; UNVERIFIED that the attribute holding it is readable in NRQL, check with the researcher agent and a read-only query);
- there is one sample per series per bucket, or the bucket is a multiple of the scrape interval (our LIVE-C measurements show rates are wrong otherwise);
- every series carries the grouping labels used in `FACET` (at most five, 5000 facets);
- a label used in a `WHERE` exists on every series (missing-label behaviour of `!=` and `NOT RLIKE` is UNVERIFIED);
- an info metric used for enrichment has value one.

Design: an `invariants` list on each converted item (string ids, fixed vocabulary), plus a data-check step that evaluates each id with one cheap read-only query (`SELECT uniques(...)`, `keyset()`, `count(*)` over a short `SINCE`), and moves the item from approximate to exact when all hold, or leaves it approximate with the failed id as the reason. Today `Confidence` has four values and the caveats live in free-text reasons, so the taxonomy has to pattern-match prose; an id list removes that and lets the census count which invariant blocks most.

### 4.3 Window, bucket and boundary-bucket audit

Their two best findings are about the interaction of range window, chart bucket and the incomplete last bucket. NRQL has the same family of traps: `TIMESERIES AUTO` picks a bucket from the time range; `rate(sum(c), 1 second)` is independent of bucket but the first and last bucket may cover less than a full bucket; a billboard over a short `SINCE` extrapolates (LIVE-C measured 4385.8 versus 720). Design: add a sweep to the live differential harness (section 7, item 3) that runs each converted rate or ratio widget at four ranges (1 hour, 6 hours, 24 hours, 7 days) with `TIMESERIES AUTO`, compares to Prometheus at the same step, and fails the construct if the error depends on range. Then fix by construct: for single-value widgets use `SINCE` and `UNTIL` aligned to whole buckets, or take the last complete bucket via a nested query with `LIMIT`; record the choice as an invariant. Their method note is worth copying as a principle: a test that varies only one of window and bucket cannot see the defect; vary both, and use the adaptive form the dashboard really emits.

### 4.4 Info-metric label enrichment

67 expressions in our census use `group_left` (verdict 4), mostly to attach labels from `*_info` series for display. Their idea is to drop the join when the right side is a plain selector of an info series, add the enrichment labels as grouping labels of the main metric, and prove they exist. NRQL design:

1. Recognise the shape: left side any vector, `* on(k) group_left(extra...) name_info{...}`, right side a plain selector (no function, no aggregation) of a metric whose name ends in an info suffix, extra labels not already on the left.
2. Data check: `SELECT keyset() FROM Metric WHERE metricName = '<main>'` and the same for the info metric. If every extra label is already an attribute of the main metric (common when the collector copies target labels), emit `FACET` on those attributes: exact in effect.
3. Otherwise fall back to the cookbook's NRQL JOIN or semi-join method (already written in `docs/user/remediation-cookbook.md`), marked approximate.
4. If the extra labels are used in a `by()` of an outer aggregation and cannot be shown to exist, refuse with a reason naming which label, as they do.

### 4.5 LogQL beyond two shapes

They refuse almost all of LogQL, so there is little to learn for coverage. What is useful is the fail-closed list for alerts: they keep LogQL alerting manual even when the panel form converts, because evaluation and threshold semantics need a higher bar than a chart. We already do the same for Loki rules (OURS, `R_LOKI_REDUCER`). NRQL design for what neither does: `{app="x"} |= "err"` is `SELECT count(*) FROM Log WHERE app = 'x' AND message LIKE '%err%'`; `|~` is `RLIKE`; `json` and `logfmt` parsers are not needed when logs are already attributes, so they become plain `WHERE` on the attribute; `sum by (l) (rate({...}[5m]))` is `rate(count(*), 1 second)` with `FACET`; `pattern`/`regexp` parsers need NRQL `capture()`, UNVERIFIED. Build this behind the same tiers (1: selector, 2: count and rate, 3: filters) and keep every parser stage that cannot be proven a no-op as a refusal.

### 4.6 Late-bound grouping variables

`sum by ($group) (...)` is a common dashboard idiom (their docs spend a long section on it). Theirs: an identifier-valued control bound into the grouping clause, with a stable column alias so the chart does not break when the choice changes, a refusal when the variable is combined with a concrete label, used with `without`, or is also used as a value elsewhere, and a validator that the variable really reached the emitted query. NRQL design: a dashboard variable of the identifier kind used as `FACET {{group}}` (N9 lists an identifier format; use in `FACET` is UNVERIFIED, test read-only with a one-widget dashboard in the sandbox), choices from the Grafana variable's options, refusal rules as theirs, and a post-conversion validator: every `{{var}}` token in the output must be a variable of the output dashboard; every source variable referenced by a source query must either be bound or appear in a disclosure.

### 4.7 Per-dashboard recipes (curated packs)

They ship a dozen hand-tuned packs for popular community dashboards, matched by the public dashboard id, falling back to an exact title. A pack holds a metric rename map, label rewrites, labels to ignore, forced metric kinds, replacement queries per panel, layout changes, and variable fixes. The shape of a pack is a good design for us; the content is theirs and must not be reproduced. For us: the same file format in our own words, content written by us from our census and live runs for Node Exporter Full (1860), kube-state-metrics v2 (13332), PostgreSQL (9628), NGINX Ingress (9614), JVM Micrometer (4701), cAdvisor (14282). The dashboards are public JSON on grafana.com, each with its own licence, so keep their JSON out of the repository and store only our overrides keyed by id and panel title.

### 4.8 Metric map with transforms

Their metric map is source-neutral and shared by both sources; an entry is a plain rename or an object with rename, attribute filter, unit scale and rate transform, and a scaffold command lists the metrics that still have no entry. Ours: `name_map.py` (134 lines, OURS) covers rename. NRQL design: add `unit_scale` (the source is bytes, the target seconds and so on), `attribute_filter` (the target metric is one name with an attribute, for example a Datadog-style `state:used`), and `kind` (counter or gauge pin, which also fixes the name-suffix heuristic), plus a `scaffold` mode that prints the metric names in the dashboards that the data check could not find. This is what removes the 27 census panels where `system_cpu_count`-like gauges look like counters.

### 4.9 Exhaustiveness tests over source schemas

They keep tests that fail when a new field is added to the model without being classified as carried or dropped, and when a widget type exists in the code but not in the supported-type registry (or the reverse). Applied to us: a test that walks the Grafana alert-rule and panel schema keys we know (from the fixtures and the census) and fails when a key is neither mapped nor listed in a "ignored, with reason" table. This is how silent loss of options like `noDataState`, `execErrState`, `keepFiringFor`, `renotify` stays out.

### 4.10 Safe auto-fix then manualise

After a live validation run they apply a short list of rewrites they can prove safe (alias quoting, reserved-word quoting) and convert whatever still fails into a manual item with the cluster's error text. NRQL analogue: validate every emitted query with a read-only NerdGraph `nrql` call (`SINCE 5 minutes ago LIMIT 1`), classify results into: query rejected (error text kept), empty (data not arriving), rows with all-null series, rows. Only the first is a conversion defect. The only automatic rewrites worth having are quoting rewrites already done in `identifier`/`quote_string`. Everything else becomes a visible placeholder widget, which we already do.

### 4.11 Disclosure rather than silence

Two small habits worth keeping and extending: every dropped dashboard variable that a query still references gets a warning naming the variable and what replaced it (for example the rate window); and every rule created without notification targets is tagged so it is obvious it notifies nobody. We already do the second. The first needs the invariant validator in 4.6.

## 5. Classification and confidence ideas

A. Replace free-text caveats with ids (4.2) and let the taxonomy classify ids, falling back to text only for legacy notes.

B. Keep `exact / approximate / manual / unsupported` and add one orthogonal axis, "binding", with values verified, asserted, guessed, missing, for each label and metric name in the NRQL (our `NameMap` and `fields.py` know which came from live data and which were assumed; the alert module already says "neither the names nor labels are confirmed against live data", so every alert conversion is approximate; binding makes that precise and lets the data check promote it).

C. A verdict ladder for the live comparison, in place of pass or fail: matches (series and values inside tolerance), shape only (same series, values differ, bounded by a hard ceiling so it can never count as a pass), differs, cannot compare (source not queryable, variables unexpanded). Their tolerance values were chosen by trial and are not portable; derive ours from the sandbox measurements (LIVE-C gives 1 to 4 percent for steady counters, which supports a 5 percent scalar band).

D. A three-colour gate per widget and per rule combining status, invariant failures and runtime evidence (rows or not). Use the colour only for review order. Do not publish a coverage percentage from it.

E. Keep feasibility independent of data presence: a translated query whose metric has not arrived is "data not arriving", not "not feasible". We already have this split in `data_check`; make the report say it in those words.

F. Parser result with diagnostics and a lossless flag (section 7, item 5), so "we parsed it but dropped part" is visible instead of becoming a clean conversion.

G. Do not copy their confidence number (0, 0.6, 0.85, 0.9): it carries no information beyond "warnings empty or not".

Rejected idea: a language-model last resort for refused queries. Reasons: untrusted text reaching output without the escaping functions breaks our rule in `CLAUDE.md`; there is no oracle to re-validate the output; and it hides the refusal that the census is trying to count.

## 6. Datadog: roadmap for rebuilding the Datadog to New Relic source

We removed our Datadog source (commit e8b27dd; the first prototype was d50cd01, monitors to alerts). Their Datadog adapter is the larger of the two and the only one that handles a second query language, so it is the better roadmap. Everything about Datadog behaviour here is READ from their adapter or KNOWLEDGE of public Datadog APIs, and must be checked against Datadog's documentation by the researcher agent before building. CLAUDE.md says the Datadog site is blocked on the corporate network, so all of this is file-based first: exported JSON in, no live API.

### 6.1 Source-side read APIs they use, and what else exists

| purpose | API they use (READ) | other public API worth adding (KNOWLEDGE) |
|---|---|---|
| list and fetch dashboards | v1 dashboards list and get, through the official Python client | dashboard lists, public dashboard sharing, notebooks |
| list, fetch, search monitors | v1 monitors: get by id, search with a query string (paginated), list with pagination | monitor downtimes (muting windows), monitor config policies, monitor notification rules (v2) |
| run a metric query to compare | v1 query endpoint | v2 timeseries query, v2 metrics tag configuration and tag lists (cardinality) |
| run a log search to compare | v2 logs events search | v2 logs aggregate; logs-based metrics definitions; log pipelines and indexes |
| credentials | env file with API key, application key, site | |
| not used by them | | SLOs and their corrections, Synthetics tests and locations, integration channel definitions (Slack, PagerDuty, webhooks), teams and users, downtime schedules, service catalogue |

Dashboards and monitors are read as plain JSON by the SDK; for us the file path (exported JSON in a folder) should come first, as it did for Grafana.

### 6.2 Monitor types

Datadog monitor `type` values (KNOWLEDGE; they key on `type` and on the shape of `query`): metric alert, query alert, log alert, composite, service check, event alert and event-v2 alert, process alert, SLO alert, synthetics alert, trace-analytics (APM) alert, RUM alert, CI pipeline and CI test alerts, audit alert, cost alert, network alert, error-tracking alert, database monitoring alerts, watchdog.

Their policy (READ): metric and query alerts and log alerts are the only families with a translated query; metric alerts that are simple thresholds are "automated", others "draft requires review"; every other family is manual-only with a family-specific reason that says what to build instead (algorithmic detections: threshold rule or ML job; composites: migrate members first then recombine; service checks: no equivalent; SLO: SLO plus burn-rate rule; synthetics: synthetic monitor; other products: rebuild against the equivalent data). A test makes sure each manual-only family has such a reason. Query shapes recognised in the metric-alert string: the plain `aggregator(last_Nm):query comparator threshold`, a formula over several queries, change and percent-change against a shifted window, shifted windows (`hour_before`, `day_before`, `week_before`, `month_before`, calendar shift with time zone, `timeshift`), and the log form `logs("...").rollup(...).last("...")`. Exactness is allowed only for sums of count-type queries, aligned gauge arithmetic, and single-query shifted formulas; the rest is flagged for review.

What New Relic offers for each (KNOWLEDGE, verify):

| Datadog monitor family | New Relic target |
|---|---|
| metric alert, query alert (threshold) | NRQL alert condition, static threshold, `FROM Metric`; window, slide-by and aggregation method map to the condition's aggregation window, streaming method and delay |
| change and percent change | NRQL with `COMPARE WITH` is not valid in conditions; use a baseline condition or a condition on a computed ratio (UNVERIFIED); mark approximate |
| shifted formulas (`week_before`) | no direct form; baseline condition with seasonal behaviour is the closest (approximate) |
| anomaly detection | baseline (anomaly) NRQL condition; algorithms differ, so manual with this as the target |
| forecast | `predictLinear` in NRQL for a rough linear equivalent; manual |
| outlier | no equivalent for peer-group outliers; manual (alert per series with `FACET` is the practical substitute) |
| log alert | NRQL condition on `Log`, `count(*)` or an aggregate of a numeric attribute |
| composite | none; closest is workflow-side correlation of issues; manual |
| service check | no check-status object; closest are NRQL on the integration's events or a heartbeat condition; manual |
| SLO alert | New Relic service levels (SLI and SLO) with burn-rate alerting; manual rebuild |
| synthetics | New Relic synthetic monitors and their failure conditions; manual rebuild |
| APM trace-analytics | NRQL on `Span` or `Transaction`; convertible once the span attribute names are mapped |
| RUM | NRQL on browser events (`PageView`, `BrowserInteraction`); needs attribute mapping |
| event alert | NRQL on custom events if the events are forwarded; otherwise manual |
| watchdog, network, cost, CI, audit | manual |

Options and notification text: Datadog `notify_no_data` and `no_data_timeframe` map to the New Relic signal-loss setting; `evaluation_delay` maps to the condition's delay; `renotify_interval`, `require_full_window`, `notify_by`, recovery thresholds and warning thresholds have no one-to-one setting and must be recorded as losses; `@slack-`, `@pagerduty-`, `@webhook-` handles are routing, so they become destinations and workflows (our `notification_ir` and emit layer already model those); conditional blocks in the message body (`is_alert`, `is_recovery`) and tag template variables need a rewrite into workflow template fields, which is a separate small translator. Never mark an enabled condition as complete when it has no notification target (the rule they use, and we do too).

### 6.3 Dashboards: widgets and query shapes

Widget types they route (READ), grouped by the New Relic visualisation I would map them to (OURS names, `core/dashboard_ir.py`; verify against the live widget list):

| Datadog widget type | their target | New Relic target |
|---|---|---|
| timeseries (line, area, bars) | line, area, stacked bar | line, area, bar |
| query value, change | single metric | billboard (change: billboard with comparison) |
| toplist, bar chart | table | bar chart or table (`FACET`, `ORDER BY`, `LIMIT`) |
| table, query table | data table | table |
| heatmap, distribution | heatmap, percentile lines | heatmap (histogram), line of `percentile()` |
| pie, treemap, sunburst | partition charts | pie; treemap and sunburst have no match: pie or table |
| scatter plot | XY | scatter is not in our widget set: table or line, approximate |
| geomap | map | map (`FACET countryCode` style) or table |
| hostmap | grouped data table | table with a warning (their choice, also right for us) |
| log stream, list stream | data table | log table or table over `Log` |
| note, free text, image, iframe | markdown, image | markdown |
| group, powerpack | section | page |
| check status, manage status, alert graph and value, SLO, event stream and timeline, trace and service map, topology, funnel, run workflow, process | markdown placeholder | placeholder with the reason; SLO widget maps to a service-level widget by hand |

Data sources inside widget queries: metrics and cloud cost; logs, log streams, RUM logs, CI pipelines; unsupported: APM, RUM, processes, network, profiles, security signals, CI tests, audit trail, events. For a first New Relic version: metrics and logs only, others blocked with a reason.

Template variables: a Datadog variable has a name, a tag, a default and an optional prefix; used as `$name` in queries. They bind a metric variable to the metrics data view and a log variable to the logs data view; a variable shared by both is flagged. Whole-scope `$scope` and grouping by a variable are refused or manual. New Relic target: a dashboard variable with `uniques(<attribute>)` as its query and `WHERE attr IN ({{var}})` in the widget; a variable shared by metrics and logs needs two variables or the same attribute name on both event types. Default `*` becomes "all" with the include-all setting (the note in `docs/research/newrelic-dashboard-variables.md` covers the limits).

Units and thresholds: Datadog `conditional_formats` (comparator, value, palette) become colour rules; custom units and axis settings are read per request. New Relic billboards have thresholds (warning, critical); axis units are limited. Keep the first three comparators (above, below, between) and record the rest as losses.

### 6.4 Metric query language and the NRQL target

Datadog metric query: space aggregator, metric, scope (tag filters with negation, `IN`, boolean combination, wildcards), `by {tags}`, function chain (`rollup`, `fill`, `as_rate`, `as_count`, `per_second`, and so on). Formulas combine named queries with arithmetic and functions. Log search: free text, `@attribute:value`, tags, ranges, wildcards, boolean and negation.

NRQL designs (UNVERIFIED unless noted):

- `avg:system.cpu.user{env:prod} by {host}` becomes `SELECT average(system.cpu.user) FROM Metric WHERE env = 'prod' FACET host TIMESERIES AUTO`, with metric and tag names remapped by a profile (the Datadog agent's metric names versus New Relic's infrastructure and OpenTelemetry names). The mapping file is the main work; start from the removed prototype's mapping and the metric-name research (`docs/research/metric-names-verified.md`).
- `as_count()` on a count metric is `sum()` over the bucket; `as_rate()` is `rate(sum(m), 1 second)`; `per_minute`, `per_hour` change the unit argument. `rollup(avg, 60)` sets the `TIMESERIES` bucket (at least 1 minute for Metric data in practice; check) and is otherwise approximate.
- `fill(zero)` and `default_zero` are a widget null-handling option or `filter(count(*), ...)` for counts; approximate.
- Percentile aggregators (`p50` to `p99`) are `percentile(m, 50)` on gauges; on a Datadog distribution metric they are a different statistic; flag.
- Scope with wildcards: `host:web*` is `LIKE 'web%'`; negation is `!=`/`NOT IN`; boolean scopes map to `AND`/`OR`; `IN` lists map to `IN`.
- Formulas: arithmetic of named queries that share scope and grouping is one SELECT with two aggregates; different filters per term use `filter()` per aggregate; different groupings stay manual (same rule they apply).
- Log search: terms to `message LIKE`, `@attr:value` to `attr = 'value'`, ranges to comparisons, wildcards to `LIKE`, negation to `NOT`; counting and grouping via `FACET`.

### 6.5 Metric naming, tags and cardinality

Their Datadog profiles map a tag name set (host, env, service, kube namespace and so on) to target field names and optionally prefix metric and tag names, with a custom file format validated against a schema, plus live discovery of which target fields exist and what type. Two practical lessons: dotted names need quoting in the target (backticks in NRQL, handled by `identifier`), and the same metric name may be flattened differently on different ingest routes. Cardinality shows up in their code only as a cap on control option lists and as a concern when generating synthetic test data (READ); I found no translation-time guard that warns when a `by {tag}` or a `by (label)` would exceed a target limit. NRQL has real limits: `FACET` at most five attributes and 5000 facets (census), 256-value `uniqueCount` exactness, and a query-per-widget cap. Add a check for these at conversion (section 7, item 12).

### 6.6 A ranked build order for the Datadog source

1. File-based reader for dashboard JSON and monitor JSON with the hand-written parsers (metric query, formula) returning diagnostics and a lossless flag; census script first (as `promql_census.py`) so the work is measured.
2. Monitors, metric and query alerts as threshold conditions with the exactness limits above, losses list per option, and the family table above as the reason source.
3. Log alerts and log search.
4. Dashboard widgets: timeseries, query value, toplist, table, note; then the rest.
5. Template variables as dashboard variables.
6. Notifications: handles to destinations, message conversion.
7. Live read-only API reader (blocked by the network rule), then SLO, synthetics and downtime inventory as report-only.

## 7. Ranked list of 15 improvements for our engine

Effort: S under a day, M a few days, L a week or more. Risk: chance of a wrong or misleading conversion, or of work that cannot be verified offline. Rank is by expected effect on end-to-end conversion and on trust, using our census (94 percent of PromQL panels are blocked by the variable gate, not by PromQL).

| # | Improvement | Why | Modules touched | Effort | Risk |
|---|---|---|---|---|---|
| 1 | Lift the variable gate for chained and regex variables (nested `IN ({{x}})` with `WHERE`, `uniques()` queries), after confirming nested variable support in the sandbox account | census: 389 of 412 panels blocked, 221 would convert; their docs show variables are where fidelity is won or lost | `dashboard_convert/variables.py`, `dashboards/` planner, `core/dashboard_ir.py` | M | M (account feature unverified) |
| 2 | Conditional exactness: invariant ids on results, evaluated by the data check, promoting approximate to exact | removes prose matching in taxonomy; makes "exact" mean something | `core/ir.py`, `core/data_check.py`, `core/taxonomy.py`, `dashboard_convert/reasons.py` | M | L |
| 3 | Read-only differential harness with ranges sweep and a verdict ladder (matches, shape only, differs, cannot compare) | their best defects were found this way; our LIVE-C is manual | new `core/parity.py`, `scripts/live-test/`, `docs/research/` results | L | M (needs live accounts; read-only) |
| 4 | Counter or gauge from stored metric type, not name suffix | census: 27 panels where the suffix heuristic is wrong | `dashboard_convert/reuse.py`, `rules2/promql.py`, `core/data_check.py`, `name_map.py` | S to M | L (attribute holding the type is UNVERIFIED) |
| 5 | Parser hygiene: single-quoted strings, upper-case aggregators, 50-operand cap raised, parse result with diagnostics and lossless flag | census: 6 plus 3 sole-blocked panels, no semantic risk | `promql_ast.py`, `dashboard_convert/promql.py`, `rules2/promql.py` | S | L |
| 6 | Comparison filters (`== 1`, `> 0`) as `WHERE` on the sample value for tiles, nested-query filter for tables; alerts move the comparison into the threshold | 108 panels, 263 expressions; largest single construct | `dashboard_convert/promql.py`, `dashboards/query_tier.py`, `sources/grafana/translate.py` | M | M (rows, not series; widget type must be a table or billboard) |
| 7 | Window and boundary-bucket rules from the sweep in 3: aligned `SINCE`/`UNTIL` for billboards, no reliance on `[w]`, record as invariant | real wrong numbers (measured 4385.8 vs 720) | `dashboard_convert/promql.py`, `dashboard_convert/display.py` | M | M |
| 8 | Info-metric enrichment (`group_left` to `_info`) with the key-set check and cookbook fallback | 67 expressions, 43 panels | `dashboards/query_tier.py`, `dashboard_convert/promql.py`, `core/data_check.py`, cookbook | M | M (semantic) |
| 9 | Late-bound `by ($var)` as `FACET {{var}}` with a variable-survival validator | common idiom; fail-closed rules are well understood | `dashboard_convert/variables.py`, `dashboard_convert/promql.py` | M | M (identifier variable use UNVERIFIED) |
| 10 | Rule ids and fired-rule trace on every conversion, exported as a catalogue | lets the census count rules, supports extension packs and regression diffs | `dashboard_convert/*`, `rules2/promql.py`, `core/report.py`, `scripts/analysis/promql_census.py` | M | L |
| 11 | Guard test: every refusal reason has a cookbook method and every cookbook method has a reason that cites it | their per-kind reason test has found gaps; our cookbook verify script exists | `scripts/improve/verify_cookbook.py`, `remediation_rules.py`, tests | S | L |
| 12 | NRQL limit checks at conversion: facet count, attributes in `FACET`, queries per widget, subquery depth, `uniques` cardinality | silent truncation is the failure mode; limits come from the census | `dashboard_convert/nrql.py`, `core/emit_limits_rules.py`, `core/data_check.py` | S to M | L |
| 13 | Metric map with `unit_scale`, `attribute_filter`, `kind`, and a scaffold mode that lists unmapped metrics | makes the names axis explicit; also prerequisite for any Datadog source | `dashboard_convert/name_map.py`, new `scaffold` command in `core/menu.py` | M | L |
| 14 | Dashboard-level live validation: run each emitted query read-only, classify rejected / empty / null series / rows, put the class in the report | quickest evidence for trust; mirrors what alerts already have | `core/data_check.py`, `dashboard_convert/translate.py`, `core/dashboard_report.py` | M | L |
| 15 | Exhaustiveness tests over source schemas (alert rule keys, panel keys, notification keys): each key mapped, listed as loss, or listed as ignored with reason | prevents silent option loss when Grafana adds fields | tests, `sources/grafana/models.py`, `rules2/fields.py` | S to M | L |

Not on the list on purpose: an LLM fallback, native query passthrough (we have no target engine for it, see 4.1), and layout packs.

## 8. Licence note: what is safe to reproduce

Safe, because public and permissively licensed or non-copyrightable facts:

- The PromQL grammar and function list: Prometheus documentation (Apache-2.0) and the Prometheus source. Our own `promql_ast.py` is already written from the grammar.
- The LogQL grammar and function list: Grafana Loki documentation and source (AGPL for the code, so describe the grammar, do not copy code; the documentation can be cited).
- Datadog query, formula, monitor and log-search syntax: Datadog public documentation and the OpenAPI specification published for its API clients (Apache-2.0 for the client libraries). Monitor type names, widget type names and API paths are facts about Datadog's interface.
- NRQL and NerdGraph: New Relic documentation; our census cites the pages (N1 to N10 in the existing notes).
- Elasticsearch, Kibana and ES|QL behaviour: Elastic documentation and the Apache-2.0 and SSPL/Elastic-licensed server sources, read the same way as here: description only.
- Public community dashboards: the JSON on grafana.com is published by each author under a licence shown on its page; do not vendor it, reference the id.
- Measurements: anything we measure ourselves against Prometheus or New Relic in the sandbox, which is where the numbers in our census came from.

Not safe from the upstream project: its rule patterns, its list of refused constructs written as a table, its reason strings, its metric and tag mapping tables, its field profile names and values, its curated pack contents, its test fixtures and parity cases, its prompt for the language-model step, and its documentation prose. The tolerances (5 percent scalar, 20 percent row count, 25 percent ceiling) are numbers I read; choose ours from our own measurements and do not cite theirs as justification.

Practical rules for contributors: do not paste from the upstream tree into an issue, commit message, test or code; do not install or run the package; if a design here needs a detail I did not give, derive it from an NRQL experiment and cite that.

## 9. Follow-ups

- Researcher agent: verify the KNOWLEDGE items in section 6 (monitor type list, API names, the metric-type attribute on `Metric`, identifier variables in `FACET`, nested variable support) and cite pages.
- Overseer: decide whether item 1 (variable gate) or item 3 (differential harness) comes first; item 1 moves the number, item 3 protects every later change.
- Nothing in this note changes code, fixtures or golden files.
